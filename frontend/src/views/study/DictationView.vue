<script setup lang="ts">
import { ref, onMounted, onActivated, onBeforeUnmount, nextTick } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { booksApi, practiceApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { TypingSession } from '../../core/typing';
import { speakWord, stopSpeaking } from '../../core/speech';
import { confirmAction } from '../../core/confirm';

/** 听写专项：复用练习接口，仅听音拼写题型（服务端 TTS 由客户端合成）。 */
const settings = useSettingsStore();
const books = ref<any[]>([]);
const bookId = ref<number | null>(null);
const chapters = ref<any[]>([]);
const chapterId = ref<number | null>(null);
const questions = ref<any[]>([]);
const cursor = ref(0);
const typed = ref('');
const typing = ref<TypingSession | null>(null);
const current = ref<any>(null);
const result = ref<'correct' | 'near' | 'wrong' | null>(null);
const boxRef = ref<HTMLElement | null>(null);
const started = ref(false);
const groupStats = ref({ wpm: 0, accuracy: 0 });
const details = ref<{ word_id: number; result: string }[]>([]);

onMounted(async () => {
  await settings.load();
  await loadBooks();
  window.addEventListener('keydown', onGlobalKey);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKey);
  stopSpeaking(); // 离开模块即静音，别在别的页面还念着
});

/** 页面级快捷键：Esc = 结束本次听写（2026-10-03） */
function onGlobalKey(e: KeyboardEvent) {
  if (document.querySelector('.el-overlay-message-box')) return; // 确认弹窗打开时交给弹窗
  if (e.key === 'Escape' && started.value) {
    e.preventDefault();
    void requestEndDictation();
  }
}

// keep-alive 激活时刷新词库列表，新建/导入的私有库立即可见
onActivated(() => {
  loadBooks();
});

async function loadBooks() {
  const { data } = await booksApi.list('all', '', 1, 500);
  books.value = data.items;
}

async function pickBook(id: number) {
  bookId.value = id;
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
}

function speak(text: string) {
  speakWord(text);
}

async function start() {
  const ids = chapterId.value ? [chapterId.value] : bookId.value ? (await booksApi.chapters(bookId.value)).data.items.map((c: any) => c.id) : [];
  if (!ids.length) {
    ElMessage.warning({ message: '请先选择词库/章节', duration: 2000, showClose: true });
    return;
  }
  const { data } = await practiceApi.session(ids, ['listening'], (await settings.load()).practice_group_size);
  questions.value = data.questions;
  cursor.value = 0;
  details.value = [];
  started.value = true;
  loadCurrent();
}

/** 结束本次听写（按钮 / Esc 入口）需确认；路由守卫内部调用走 silent=true */
async function requestEndDictation() {
  const ok = await confirmAction({
    title: '结束听写',
    message: '确定结束本次听写？已作答的词均已记录，未作答的词不计分。',
    confirmText: '结束听写',
    cancelText: '继续听写',
  });
  if (ok) endDictation();
}

// 中途结束本次听写：已作答的词均已入库，只重置本地会话
function endDictation(silent = false) {
  started.value = false;
  questions.value = [];
  cursor.value = 0;
  typed.value = '';
  typing.value = null;
  current.value = null;
  result.value = null;
  details.value = [];
  stopSpeaking(); // 结束即静音
  if (!silent) ElMessage.info({ message: '本次听写已结束，已完成的词均已记录', duration: 2000, showClose: true });
}

// 离开听写模块：进行中的听写必须先决定——结束本次听写，或留下继续；
// 不允许带着未完成的听写离开（回来时不该还停在原题上）。
onBeforeRouteLeave(async () => {
  if (!started.value) return true;
  const ok = await confirmAction({
    title: '结束听写',
    message: '当前还有未完成的听写，离开前是否结束本次听写？',
    confirmText: '结束听写',
    cancelText: '继续听写',
  });
  if (!ok) return false; // 留在本页继续听写
  endDictation(true);
  return true;
});

function loadCurrent() {
  current.value = questions.value[cursor.value] || null;
  if (!current.value) {
    started.value = false;
    ElMessage.success({ message: '本组听写完成', duration: 2000, showClose: true });
    return;
  }
  result.value = null;
  typed.value = '';
  typing.value = new TypingSession('');
  // 只播单词本身：曾把「第 N 题」也送进 TTS，中文串被 edge-tts 按英语念，题号读成 one/two（2026-10-03 修）
  speak(current.value.payload.spelling);
  nextTick(() => boxRef.value?.focus());
}

/** word 为题干词拼写（服务端下发） */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    if (e.repeat) return; // 长按回车不得连交/连跳
    return result.value ? next() : submit();
  }
  if (e.key === 'Backspace') {
    typed.value = typed.value.slice(0, -1);
    return;
  }
  if (e.key.length === 1) {
    typed.value += e.key;
    typing.value?.key(e.key);
  }
}

async function submit() {
  const body = {
    qid: current.value.qid,
    word_id: current.value.word_id,
    type: 'listening',
    typed: typed.value,
    duration_ms: typing.value?.detail().duration_ms ?? 0,
    detail: typing.value?.detail(),
    request_id: uuid(),
  };
  const { data } = await practiceApi.answer(body);
  result.value = data.result;
  details.value.push({ word_id: current.value.word_id, result: data.result });
  computeStats();
}

function computeStats() {
  const total = details.value.length;
  const ok = details.value.filter((d) => d.result === 'correct').length;
  groupStats.value = { wpm: typing.value?.wpm() ?? 0, accuracy: total ? Math.round((ok / total) * 100) : 0 };
}

function next() {
  cursor.value += 1;
  loadCurrent();
}
</script>

<template>
  <PageShell title="听写专项" subtitle="听音拼写（TTS）">
    <template #actions>
      <el-button v-if="started" type="danger" plain @click="requestEndDictation">结束本次听写 <kbd>Esc</kbd></el-button>
    </template>
    <div class="scope-bar">
      <el-select v-model="bookId" placeholder="选择词库" style="width: 220px" @change="pickBook">
        <el-option v-for="b in books" :key="b.id" :label="b.name" :value="b.id" />
      </el-select>
      <el-select v-model="chapterId" placeholder="整本词库" clearable style="width: 220px">
        <el-option v-for="c in chapters" :key="c.id" :label="c.name" :value="c.id" />
      </el-select>
      <el-button type="primary" @click="start">开始听写</el-button>
      <el-tag v-if="started">第 {{ cursor + 1 }} / {{ questions.length }} 题</el-tag>
      <el-tag type="success" v-if="details.length">WPM {{ groupStats.wpm }} · 准确率 {{ groupStats.accuracy }}%</el-tag>
    </div>

    <el-card v-if="started && current" shadow="never" class="listen-card">
      <div class="play-row">
        <el-button type="primary" circle size="large" @click="speak(current.payload.spelling)">
          <el-icon size="24"><VideoPlay /></el-icon>
        </el-button>
        <span class="hint">点击重新播放，听音后输入拼写</span>
      </div>
      <div ref="boxRef" class="typing-box" tabindex="0" @keydown.prevent="onKeydown">
        <span class="display">{{ typed }}<span class="caret">|</span></span>
      </div>
      <el-button type="primary" size="large" @click="submit">提交</el-button>
      <el-alert
        v-if="result"
        :type="result === 'correct' ? 'success' : result === 'near' ? 'warning' : 'error'"
        :closable="false"
        class="fb"
        :title="`正确答案：${current.payload.spelling}${result === 'near' ? '（近似，差一个字母）' : result === 'wrong' ? '' : ''}`"
      />
      <el-button v-if="result" type="primary" @click="next">下一题 ↵</el-button>
    </el-card>
    <el-empty v-else description="选择范围后开始听写" />
  </PageShell>
</template>

<style scoped>
.scope-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}
.listen-card {
  max-width: 560px;
  margin: 0 auto;
  text-align: center;
}
.play-row {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.hint {
  color: var(--wt-text-4);
  font-size: 13px;
}
.typing-box {
  margin: 16px auto;
  padding: 16px;
  border: 2px solid var(--wt-border);
  border-radius: 8px;
  outline: none;
}
.typing-box:focus {
  border-color: var(--wt-primary);
}
.display {
  font-size: 22px;
  font-family: Consolas, monospace;
  letter-spacing: 2px;
}
.caret {
  color: var(--wt-primary);
  animation: blink 1s infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
.fb {
  margin: 12px 0;
}
</style>
