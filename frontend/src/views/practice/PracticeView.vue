<script setup lang="ts">
import { ref, computed, nextTick } from 'vue';
import { ElMessage } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { booksApi, practiceApi, wrongbookApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { ApiError } from '../../api/http';
import { enqueueFailed } from '../../core/offline';
import { speakWord } from '../../core/speech';

const settings = useSettingsStore();

const books = ref<any[]>([]);
const bookId = ref<number | null>(null);
const chapters = ref<any[]>([]);
const selectedChapters = ref<number[]>([]);
const types = ref<string[]>(['dictation']);
const running = ref(false);
const finished = ref(false);

const questions = ref<any[]>([]);
const cursor = ref(0);
const typed = ref('');
const feedback = ref<{ result: string; proficiency: number; streak: number } | null>(null);
const answerIds = new Set<string>();
const reshowIds = ref<string[]>([]); // D23：答错 50% 重现（组内一次）
const answeredResults = ref<string[]>([]);
const wrongWords = ref<number[]>([]);

const current = computed(() => questions.value[cursor.value] ?? null);
const progress = computed(() => `${Math.min(cursor.value + 1, questions.value.length)} / ${questions.value.length}`);

const typeOptions = [
  { value: 'dictation', label: '释义 → 默写' },
  { value: 'choice', label: '四选一' },
  { value: 'listening', label: '听音拼写' },
  { value: 'cloze', label: '例句挖空' },
];

let firstKeyTs = 0;
let lastKeyTs = 0;

async function loadBooks() {
  const { data } = await booksApi.list('all');
  books.value = data.items;
}

async function pickBook(id: number) {
  bookId.value = id;
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
  // 默认全选章节，用户可自行取消勾选
  selectedChapters.value = data.items.map((c: any) => c.id);
}

async function start() {
  if (!bookId.value) {
    ElMessage.warning('请先选择词库');
    return;
  }
  if (!selectedChapters.value.length) {
    ElMessage.warning('请至少选择一个章节');
    return;
  }
  if (!types.value.length) {
    ElMessage.warning('请至少勾选一种题型');
    return;
  }
  const s = await settings.load();
  try {
    const { data } = await practiceApi.session(selectedChapters.value, types.value, s.practice_group_size);
    questions.value = data.questions;
  } catch {
    return;
  }
  cursor.value = 0;
  answerIds.clear();
  reshowIds.value = [];
  answeredResults.value = [];
  wrongWords.value = [];
  running.value = true;
  finished.value = false;
  armTyping();
  playListening();
}

function armTyping() {
  typed.value = '';
  firstKeyTs = 0;
  lastKeyTs = 0;
  nextTick(() => (document.querySelector('.typing-box') as HTMLElement)?.focus());
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') return submit();
  if (e.key === 'Backspace') {
    typed.value = typed.value.slice(0, -1);
    return;
  }
  if (e.key.length === 1) {
    if (!firstKeyTs) firstKeyTs = Date.now();
    lastKeyTs = Date.now();
    typed.value += e.key;
  }
}

function trackResult(result: string, wordId: number) {
  answeredResults.value.push(result);
  if (result === 'wrong') wrongWords.value.push(wordId);
  const q = current.value;
  if (result === 'wrong' && !answerIds.has(q?.qid) && Math.random() < 0.5) {
    reshowIds.value.push(q.qid);
  }
}

async function submit() {
  const q = current.value;
  if (!q) return;
  const body: any = {
    qid: q.qid,
    word_id: q.word_id,
    type: q.type,
    duration_ms: lastKeyTs && firstKeyTs ? lastKeyTs - firstKeyTs : 0,
    detail: { v: 1, duration_ms: lastKeyTs && firstKeyTs ? lastKeyTs - firstKeyTs : 0, keys: [] },
    request_id: uuid(),
    typed: typed.value,
  };
  try {
    const { data } = await practiceApi.answer(body);
    feedback.value = { result: data.result, proficiency: data.proficiency, streak: data.streak_correct };
    trackResult(data.result, q.word_id);
  } catch (e) {
    if (e instanceof ApiError && e.code === 'NETWORK_ERROR') {
      enqueueFailed({ request_id: body.request_id, url: '/api/v1/practice/answer', body });
      ElMessage.warning('网络异常，本题已进入离线重放队列');
      advance();
    }
    return;
  }
}

function choose(key: string) {
  if (feedback.value) return;
  const q = current.value;
  const body = { qid: q.qid, word_id: q.word_id, type: q.type, choice_key: key, duration_ms: 0, request_id: uuid() };
  practiceApi
    .answer(body)
    .then(({ data }) => {
      feedback.value = { result: data.result, proficiency: data.proficiency, streak: data.streak_correct };
      trackResult(data.result, q.word_id);
    })
    .catch((e) => {
      if (e instanceof ApiError && e.code === 'NETWORK_ERROR') {
        enqueueFailed({ request_id: body.request_id, url: '/api/v1/practice/answer', body });
        advance();
      }
    });
}

function advance() {
  answerIds.add(current.value?.qid);
  feedback.value = null;
  // D23：重现题插入队尾（组内一次）
  if (reshowIds.value.length) {
    const rid = reshowIds.value.shift();
    const original = questions.value.find((q) => q.qid === rid);
    if (original && !questions.value.some((q) => q.qid === `${rid}-reshow`)) {
      questions.value.push({ ...original, qid: `${rid}-reshow` });
    }
  }
  cursor.value += 1;
  if (cursor.value >= questions.value.length) {
    running.value = false;
    finished.value = true;
  } else {
    armTyping();
    playListening();
  }
}

/** 听音题自动播报（有道发音，TTS 兜底） */
function playListening() {
  const q = questions.value[cursor.value];
  if (q && q.type === 'listening') speakWord(q.payload.spelling);
}

function endGroup() {
  running.value = false;
  finished.value = true;
}
</script>

<template>
  <PageShell title="练习模式" subtitle="四种题型 · 组内断点续练 · 离线重放">
    <template #actions>
      <el-button v-if="running" @click="endGroup">手动结束</el-button>
    </template>

    <el-card v-if="!running && !finished" shadow="never" class="config-card">
      <el-form label-width="90px">
        <el-form-item label="词库">
          <el-select v-model="bookId" placeholder="选择词库" style="width: 220px" @change="pickBook" @focus="loadBooks">
            <el-option v-for="b in books" :key="b.id" :label="b.name" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="章节">
          <el-select v-model="selectedChapters" multiple collapse-tags placeholder="多选章节（已默认全选）" style="width: 320px" :disabled="!bookId">
            <el-option v-for="c in chapters" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="题型">
          <el-checkbox-group v-model="types">
            <el-checkbox v-for="t in typeOptions" :key="t.value" :value="t.value">{{ t.label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="组规格">
          <span>{{ settings.settings?.practice_group_size ?? 20 }} 题（可在设置页调整 10–50）</span>
        </el-form-item>
        <el-button type="primary" size="large" :disabled="!bookId" @click="start">开始练习</el-button>
        <span v-if="!bookId" class="start-hint">先选择词库，章节将默认全选</span>
      </el-form>
    </el-card>

    <el-card v-if="running && current" shadow="never" class="q-card">
      <div class="q-head">
        <el-tag>{{ progress }}</el-tag>
        <el-tag type="info">{{ typeOptions.find((t) => t.value === current.type)?.label }}</el-tag>
      </div>

      <!-- 默写 -->
      <template v-if="current.type === 'dictation'">
        <p class="prompt">{{ current.payload.meaning }}</p>
        <div class="typing-box" tabindex="0" @keydown.prevent="onKeydown">
          <span class="display">{{ typed }}<span class="caret">|</span></span>
        </div>
      </template>

      <!-- 听音 -->
      <template v-else-if="current.type === 'listening'">
        <p class="prompt">听音拼写</p>
        <div class="replay-row">
          <el-button circle type="primary" size="large" @click="speakWord(current.payload.spelling)">
            <el-icon size="22"><VideoPlay /></el-icon>
          </el-button>
        </div>
        <div class="typing-box" tabindex="0" @keydown.prevent="onKeydown">
          <span class="display">{{ typed }}<span class="caret">|</span></span>
        </div>
      </template>

      <!-- 挖空 -->
      <template v-else-if="current.type === 'cloze'">
        <p class="prompt cloze">{{ current.payload.sentence_masked }}</p>
        <div class="typing-box" tabindex="0" @keydown.prevent="onKeydown">
          <span class="display">{{ typed }}<span class="caret">|</span></span>
        </div>
      </template>

      <!-- 选择 -->
      <template v-else-if="current.type === 'choice' && !feedback">
        <p class="prompt">{{ current.payload.phonetic || '选出正确释义' }}</p>
        <div class="options">
          <el-button
            v-for="o in current.payload.options"
            :key="o.key"
            size="large"
            class="option-btn"
            @click="choose(o.key)"
          >
            <b class="key">{{ o.key }}.</b> {{ o.text }}
          </el-button>
        </div>
      </template>

      <div
        v-if="feedback && feedback.result"
        class="fb"
        :class="feedback.result"
      >
        {{ feedback.result === 'correct' ? '正确 ✔' : feedback.result === 'near' ? '近似正确（差一个字母）' : '答错 ✘' }}
      </div>

      <div class="foot-row" v-if="!(current.type === 'choice' && !feedback)">
        <el-button v-if="!feedback" type="primary" size="large" @click="submit">提交</el-button>
        <el-button v-if="feedback" type="primary" size="large" @click="advance">下一题</el-button>
      </div>
    </el-card>

    <el-card v-if="finished" shadow="never" class="q-card">
      <el-result
        icon="success"
        title="本组练习结束"
        :sub-title="`共答 ${answeredResults.length} 题，答对 ${answeredResults.filter((r) => r === 'correct').length} 题，错词 ${wrongWords.length} 个（已自动计入错题本）`"
      />
      <div class="foot-row">
        <el-button type="primary" @click="finished = false">再来一组</el-button>
      </div>
    </el-card>
  </PageShell>
</template>

<style scoped>
.config-card {
  max-width: 640px;
}
.q-card {
  max-width: 640px;
  margin: 0 auto;
}
.q-head {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.prompt {
  font-size: 20px;
  color: var(--wt-text);
  text-align: center;
  margin: 24px 0;
}
.prompt.cloze {
  font-family: Georgia, serif;
  font-style: italic;
  color: var(--wt-text-2);
}
.typing-box {
  margin: 12px auto;
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
.options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 16px;
}
.option-btn {
  height: 52px;
  justify-content: flex-start;
}
.option-btn .key {
  margin-right: 8px;
  color: var(--wt-primary);
}
.fb {
  text-align: center;
  font-size: 18px;
  font-weight: 600;
  margin: 16px 0;
}
.fb.correct {
  color: var(--wt-success);
}
.fb.near {
  color: var(--wt-warning);
}
.fb.wrong {
  color: var(--wt-danger);
}
.foot-row {
  display: flex;
  justify-content: center;
}
.start-hint {
  margin-left: 12px;
  font-size: 13px;
  color: var(--wt-text-4);
}
</style>
