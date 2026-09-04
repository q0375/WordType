<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { booksApi, practiceApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { TypingSession } from '../../core/typing';
import { speakWord } from '../../core/speech';

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
const started = ref(false);
const groupStats = ref({ wpm: 0, accuracy: 0 });
const details = ref<{ word_id: number; result: string }[]>([]);

onMounted(async () => {
  await settings.load();
  const { data } = await booksApi.list('all');
  books.value = data.items;
});

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
    ElMessage.warning('请先选择词库/章节');
    return;
  }
  const { data } = await practiceApi.session(ids, ['listening'], (await settings.load()).practice_group_size);
  questions.value = data.questions;
  cursor.value = 0;
  details.value = [];
  started.value = true;
  loadCurrent();
}

function loadCurrent() {
  current.value = questions.value[cursor.value] || null;
  if (!current.value) {
    started.value = false;
    ElMessage.success('本组听写完成');
    return;
  }
  result.value = null;
  typed.value = '';
  typing.value = new TypingSession('');
  speak(`第 ${cursor.value + 1} 题`);
  setTimeout(() => speak(current.value.payload.spelling), 600);
}

/** word 为题干词拼写（服务端下发） */
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') return submit();
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
      <div class="typing-box" tabindex="0" @keydown.prevent="onKeydown">
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
      <el-button v-if="result" type="primary" @click="next">下一题</el-button>
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
  color: #9ca3af;
  font-size: 13px;
}
.typing-box {
  margin: 16px auto;
  padding: 16px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  outline: none;
}
.typing-box:focus {
  border-color: #2563eb;
}
.display {
  font-size: 22px;
  font-family: Consolas, monospace;
  letter-spacing: 2px;
}
.caret {
  color: #2563eb;
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
