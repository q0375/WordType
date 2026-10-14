<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onActivated } from 'vue';
import { partialRatio } from '../../core/judge';
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router';
import { onBeforeUnmount } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { booksApi, practiceApi, wrongbookApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { ApiError } from '../../api/http';
import { enqueueFailed } from '../../core/offline';
import { speakWord } from '../../core/speech';
import { isForbiddenKey } from '../../core/anticheat';
import { confirmAction } from '../../core/confirm';
import { splitSentence } from '../../core/text';

const settings = useSettingsStore();
const route = useRoute();
const router = useRouter();

const TYPE_LABEL: Record<string, string> = {
  dictation: '释义 → 默写', choice: '四选一', listening: '听音拼写', cloze: '例句挖空',
};

/**
 * 仪表盘建议直达：/practice?word_ids=1,2,3&type=cloze
 * 高错误率词的专项练习建议点「去执行」时带词进来，跳过章节选择直接开跑（2026-10-04）。
 * 注意：query 会被 router.replace 摘除，故先把 ids/题型快照成普通 ref 再开跑，
 * 否则 await replace 之后 isDirect 已变 false，start() 会掉进「请先选择词库」分支。
 */
const routeWordIds = computed<number[]>(() => {
  const raw = route.query.word_ids;
  const s = Array.isArray(raw) ? raw[0] : raw;
  if (!s) return [];
  return String(s).split(',').map((x) => Number(x.trim())).filter((n) => Number.isInteger(n) && n > 0);
});
const routeType = computed(() => {
  const raw = route.query.type;
  const t = Array.isArray(raw) ? raw[0] : raw;
  return typeof t === 'string' && t in TYPE_LABEL ? t : '';
});
/** 本组是否由仪表盘建议发起（query 会被摘除，故用普通 ref 记录来源与词表） */
const fromAdvice = ref(false);
const adviceWordIds = ref<number[]>([]);

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
const feedback = ref<{ result: string; proficiency: number; streak: number; answer?: string; meaning?: string; ratio?: number } | null>(null);

// 展示用得分比：优先后端值；旧后端响应缺 ratio 字段时按同源 difflib 算法本地兜底（2026-09-22 修复显示恒 0%）
function ratioOf(data: any, typedSent: string): number {
  if (typeof data?.ratio === 'number') return data.ratio;
  return data?.answer ? partialRatio(typedSent || '', data.answer) : 0;
}
const answerIds = new Set<string>();
const reshowIds = ref<string[]>([]); // D23：答错 50% 重现（组内一次）
const answeredResults = ref<string[]>([]);
const wrongWords = ref<number[]>([]);

const current = computed(() => questions.value[cursor.value] ?? null);

// 挖空题：word.example 是「英文句 中文翻译」混在一个字段里，按第一个 CJK 字符拆两行展示（2026-10-04）
const clozeParts = computed(() => splitSentence(current.value?.payload?.sentence_masked));
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
  const { data } = await booksApi.list('all', '', 1, 500);
  books.value = data.items;
}

onMounted(async () => {
  loadBooks();
  window.addEventListener('keydown', onGlobalKey);
  const ids = routeWordIds.value;
  if (ids.length) {
    // 建议直达：快照词表与题型 → 清 query（避免刷新/重进 keep-alive 重复开跑）→ 直接开跑
    fromAdvice.value = true;
    adviceWordIds.value = ids;
    if (routeType.value) types.value = [routeType.value];
    await router.replace({ path: '/practice' });
    await start();
  }
});

onActivated(() => {
  loadBooks();
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKey);
});

/** 页面级快捷键：Esc = 结束本组；结束页回车 = 再来一组（2026-10-03） */
function onGlobalKey(e: KeyboardEvent) {
  if (document.querySelector('.el-overlay-message-box')) return; // 确认弹窗打开时交给弹窗
  if (e.repeat) return;
  if (e.key === 'Escape' && running.value) {
    e.preventDefault();
    void requestEndGroup();
    return;
  }
  if (e.key === 'Enter' && finished.value) {
    if ((document.activeElement?.tagName || '').toLowerCase() === 'button') return; // 焦点在按钮上走原生
    e.preventDefault();
    finished.value = false; // 再来一组：回到配置页
  }
}

// keep-alive 激活时刷新词库列表，新建/导入的私有库立即可见
onActivated(() => {
  loadBooks();
});

async function pickBook(id: number) {
  bookId.value = id;
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
  // 默认全不选，由用户通过「全选」或逐个勾选
  selectedChapters.value = [];
}

const allSelected = computed(() => chapters.value.length > 0 && selectedChapters.value.length === chapters.value.length);
const indeterminate = computed(() => selectedChapters.value.length > 0 && selectedChapters.value.length < chapters.value.length);

function toggleAll(checked: any) {
  selectedChapters.value = checked ? chapters.value.map((c: any) => c.id) : [];
}

async function start() {
  const s = await settings.load();
  // 建议直达：跳过章节/词库校验，按 adviceWordIds 组卷（快照值，不读已被清掉的 query）
  if (adviceWordIds.value.length) {
    await runSession(s.practice_group_size, adviceWordIds.value);
    return;
  }
  if (!bookId.value) {
    ElMessage.warning({ message: '请先选择词库', duration: 2000, showClose: true });
    return;
  }
  if (!selectedChapters.value.length) {
    ElMessage.warning({ message: '请至少选择一个章节', duration: 2000, showClose: true });
    return;
  }
  if (!types.value.length) {
    ElMessage.warning({ message: '请至少勾选一种题型', duration: 2000, showClose: true });
    return;
  }
  await runSession(s.practice_group_size, []);
}

async function runSession(groupSize: number, wordIds: number[]) {
  try {
    const { data } = await practiceApi.session(selectedChapters.value, types.value, groupSize, wordIds);
    questions.value = data.questions || [];
    // 勾了却一题都出不来（如挖空无例句）：明确告知，否则只勾 cloze 会整页空白
    if (Array.isArray(data.skipped) && data.skipped.length) {
      const names = data.skipped.map((x: any) => TYPE_LABEL[x.type] || x.type).join('、');
      ElMessage.warning({
        message: `所选范围内没有可用的「${names}」题目，已跳过该题型`,
        duration: 4000, showClose: true,
      });
    }
  } catch {
    return; // 错误已由请求层统一提示（如 EMPTY_QUESTION_POOL / EMPTY_WORD_POOL）
  }
  // 后端已保证至少一种题型可出题；仍为空则视为异常，不进 running 造成白屏
  if (!questions.value.length) {
    ElMessage.warning({ message: '所选范围内没有可练习的题目，请调整章节或题型', duration: 3000, showClose: true });
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
  nextTick(() => {
    const box = document.querySelector('.typing-box') as HTMLElement | null;
    if (box) {
      box.focus();
      return;
    }
    // 四选一没有输入框：聚焦选项区，使 A/B/C/D 与 1/2/3/4 可直接作答（2026-10-03）
    (document.querySelector('.options') as HTMLElement | null)?.focus();
  });
}

/** 四选一快捷键映射：ABC / 123 均可用（与考核端 ExamView 同一映射） */
const CHOICE_KEY_MAP: Record<string, string> = {
  '1': 'A', '2': 'B', '3': 'C', '4': 'D',
  a: 'A', b: 'B', c: 'C', d: 'D',
};

function onKeydown(e: KeyboardEvent) {
  // 四选一：直接按键作答（不经过输入判定，Enter 留给「再来一组/下一题」等）
  if (current.value?.type === 'choice' && !feedback.value) {
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const key = CHOICE_KEY_MAP[e.key.toLowerCase()];
    if (key && (current.value.payload.options || []).some((o: any) => o.key === key)) {
      e.preventDefault();
      choose(key);
    }
    return;
  }
  if (isForbiddenKey(e)) {
    // 防作弊（D37）：Ctrl/Alt/Insert 等键按下即清空输入
    typed.value = '';
    ElMessage.warning({ message: '检测到禁用按键（Ctrl/Alt/Insert），输入已清空', duration: 2000, showClose: true });
    return;
  }
  if (e.key === 'Enter') {
    if (e.repeat) return; // 长按回车不得连交/连跳
    return feedback.value ? advance() : submit();
  }
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
  if (!q || feedback.value) return;
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
    feedback.value = { result: data.result, proficiency: data.proficiency, streak: data.streak_correct, answer: data.answer, meaning: data.meaning, ratio: ratioOf(data, typed.value) };
    trackResult(data.result, q.word_id);
  } catch (e) {
    if (e instanceof ApiError && e.code === 'NETWORK_ERROR') {
      enqueueFailed({ request_id: body.request_id, url: '/api/v1/practice/answer', body });
      ElMessage.warning({ message: '网络异常，本题已进入离线重放队列', duration: 2000, showClose: true });
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
      feedback.value = { result: data.result, proficiency: data.proficiency, streak: data.streak_correct, answer: data.answer, meaning: data.meaning, ratio: ratioOf(data, typed.value) };
      trackResult(data.result, q.word_id);
      nextTick(() => (document.querySelector('.fb') as HTMLElement | null)?.focus());
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

/** 结束本组（按钮 / Esc 入口）：未作答题目不计分、不进错题本，故需确认 */
async function requestEndGroup() {
  const remaining = questions.value.length - answeredResults.value.length;
  const ok = await confirmAction({
    title: '结束练习',
    message: remaining > 0
      ? `本组还剩 ${remaining} 题未作答，结束后本组结束（未作答不计分、不进错题本）。确定结束？`
      : '确定结束本组练习？',
    confirmText: '结束练习',
    cancelText: '继续练习',
  });
  if (ok) endGroup();
}

/** 清空本组现场：离开模块后不得残留进度（keep-alive 缓存会原样恢复页面） */
function resetGroup() {
  running.value = false;
  finished.value = false;
  fromAdvice.value = false;
  adviceWordIds.value = [];
  questions.value = [];
  cursor.value = 0;
  typed.value = '';
  feedback.value = null;
  answerIds.clear();
  reshowIds.value = [];
  answeredResults.value = [];
  wrongWords.value = [];
}

// 离开练习模块：进行中的组必须先决定——结束本组，或留下继续练。
// 无论进行中还是已结束，离开时一律清空现场，回来时回到配置页而非「本组练习结束」页。
onBeforeRouteLeave(async () => {
  if (!running.value && !finished.value) return true;
  if (running.value) {
    const ok = await confirmAction({
      title: '结束练习',
      message: '当前还有未完成的练习组，离开前是否结束本组练习？',
      confirmText: '结束练习',
      cancelText: '继续练习',
    });
    if (!ok) return false; // 留在本页继续练
    endGroup();
  }
  resetGroup();
  return true;
});
</script>

<template>
  <PageShell title="练习模式" subtitle="四种题型 · 组内断点续练 · 离线重放">
    <template #actions>
      <el-button v-if="running" @click="requestEndGroup">手动结束 <kbd>Esc</kbd></el-button>
    </template>

    <el-card v-if="!running && !finished" shadow="never" class="config-card">
      <el-form label-width="90px">
        <el-form-item label="词库">
          <el-select v-model="bookId" placeholder="选择词库" style="width: 220px" @change="pickBook" @focus="loadBooks">
            <el-option v-for="b in books" :key="b.id" :label="b.name" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="章节">
          <div class="chapter-list">
            <div class="chapter-all">
              <el-checkbox
                :model-value="allSelected"
                :indeterminate="indeterminate"
                :disabled="!bookId || !chapters.length"
                @change="toggleAll"
              >全选（共 {{ chapters.length }} 章）</el-checkbox>
            </div>
            <el-checkbox-group v-model="selectedChapters" class="chapter-scroll" :disabled="!bookId">
              <el-checkbox v-for="c in chapters" :key="c.id" :value="c.id" class="chapter-item">{{ c.name }}</el-checkbox>
            </el-checkbox-group>
            <p v-if="bookId && !chapters.length" class="chapter-empty">该词库暂无章节，先去词库页添加</p>
          </div>
        </el-form-item>
        <el-form-item label="题型">
          <el-checkbox-group v-model="types">
            <el-checkbox v-for="t in typeOptions" :key="t.value" :value="t.value">{{ t.label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="组规格">
          <span>{{ settings.settings?.practice_group_size ?? 20 }} 题（可在设置页调整 1–9999，超过所选单词数时按实际词数出题）</span>
        </el-form-item>
        <el-button type="primary" size="large" :disabled="!bookId" @click="start">开始练习</el-button>
        <span v-if="!bookId" class="start-hint">先选择词库，再勾选要练的章节</span>
      </el-form>
    </el-card>

    <el-card v-if="running && current" shadow="never" class="q-card">
      <div class="q-head">
        <el-tag>{{ progress }}</el-tag>
        <el-tag type="info">{{ typeOptions.find((t) => t.value === current.type)?.label }}</el-tag>
        <el-tag v-if="fromAdvice" type="warning" size="small">来自「今日建议」的专项练习</el-tag>
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

      <!-- 挖空：例句英文一行（含挖空）+ 例句中文翻译一行 + 单词释义弱提示（2026-10-04） -->
      <template v-else-if="current.type === 'cloze'">
        <p class="prompt cloze">{{ clozeParts.en }}</p>
        <p class="prompt cloze-mean">{{ clozeParts.cn }}</p>
        <p class="prompt cloze-word">{{ current.payload.meaning }}</p>
        <div class="typing-box" tabindex="0" @keydown.prevent="onKeydown">
          <span class="display">{{ typed }}<span class="caret">|</span></span>
        </div>
      </template>

      <!-- 选择 -->
      <template v-else-if="current.type === 'choice' && !feedback">
        <p class="prompt">{{ current.payload.phonetic || '选出正确释义' }}</p>
        <div class="options" tabindex="0" @keydown.prevent="onKeydown">
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
        <p class="options-hint">可用键盘 <kbd>A</kbd><kbd>B</kbd><kbd>C</kbd><kbd>D</kbd> 或 <kbd>1</kbd><kbd>2</kbd><kbd>3</kbd><kbd>4</kbd> 选择答案</p>
      </template>

      <div
        v-if="feedback && feedback.result"
        class="fb"
        :class="feedback.result"
        tabindex="0"
        @keydown.enter.prevent="advance"
      >
        <div class="fb-line">
          <!-- 非完全正确统一展示得分比（2026-09-21 口径统一）；四选一非对即错，不适用比例 -->
          {{ feedback.result === 'correct' ? '正确 ✔' : current.type === 'choice' ? '答错 ✘' : `不完全正确（得分比 ${Math.round((feedback.ratio ?? 0) * 100)}%）✘` }}（回车=下一题）
        </div>
        <div v-if="feedback.answer" class="fb-answer">
          正确答案：<b>{{ feedback.answer }}</b><span v-if="feedback.meaning">（{{ feedback.meaning }}）</span>
        </div>
      </div>

      <div class="foot-row" v-if="!(current.type === 'choice' && !feedback)">
        <el-button v-if="!feedback" type="primary" size="large" @click="submit">提交</el-button>
        <el-button v-if="feedback" type="primary" size="large" @click="advance">下一题 ↵</el-button>
      </div>
    </el-card>

    <el-card v-if="finished" shadow="never" class="q-card">
      <el-result
        icon="success"
        title="本组练习结束"
        :sub-title="`共答 ${answeredResults.length} 题，答对 ${answeredResults.filter((r) => r === 'correct').length} 题，错词 ${wrongWords.length} 个（已自动计入错题本）`"
      />
      <div class="foot-row">
        <el-button type="primary" @click="finished = false">再来一组 <kbd>↵</kbd></el-button>
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
  word-break: break-word;
  overflow-wrap: anywhere;
}
.prompt.cloze {
  font-family: Georgia, serif;
  font-style: italic;
  color: var(--wt-text-2);
}
/* 挖空题第二行：例句的中文翻译（非斜体、弱化），与英文题干分两行 */
.prompt.cloze-mean {
  font-family: inherit;
  font-style: normal;
  font-size: 16px;
  color: var(--wt-text-4);
  margin: -12px 0 0;
}
/* 第三行：答案单词的释义弱提示（更小更淡，只作辅助） */
.prompt.cloze-word {
  font-size: 13px;
  color: var(--wt-text-4);
  opacity: 0.75;
  margin: 6px 0 20px;
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
  word-break: break-all;
  overflow-wrap: anywhere;
  line-height: 1.5;
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
.options:focus-visible {
  outline: 2px solid var(--wt-primary);
  outline-offset: 4px;
  border-radius: 6px;
}
.options-hint {
  color: var(--wt-text-4);
  font-size: 13px;
  text-align: center;
  margin: 10px 0 0;
}
.options-hint kbd {
  display: inline-block;
  min-width: 20px;
  padding: 1px 5px;
  margin: 0 2px;
  border: 1px solid var(--wt-border);
  border-bottom-width: 2px;
  border-radius: 4px;
  background: var(--wt-bg-2, #f9fafb);
  font-size: 12px;
  line-height: 1.5;
  text-align: center;
}
.options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 16px;
}
.option-btn {
  height: auto;
  min-height: 52px;
  white-space: normal;
  text-align: left;
  justify-content: flex-start;
  word-break: break-word;
  overflow-wrap: anywhere;
  padding-top: 6px;
  padding-bottom: 6px;
  line-height: 1.5;
}
.option-btn .key {
  margin-right: 8px;
  color: var(--wt-primary);
  white-space: nowrap;
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
.fb-line {
  line-height: 1.6;
}
.fb-answer {
  margin-top: 6px;
  font-size: 15px;
  font-weight: 500;
  color: var(--wt-text-2);
}
.fb-answer b {
  font-size: 18px;
  font-family: Consolas, monospace;
  letter-spacing: 1px;
  color: var(--wt-success);
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
.chapter-list {
  width: 100%;
  max-width: 460px;
  border: 1px solid var(--wt-border);
  border-radius: 8px;
  padding: 8px 12px;
}
.chapter-all {
  padding-bottom: 6px;
  border-bottom: 1px solid var(--wt-border);
  margin-bottom: 6px;
}
.chapter-scroll {
  max-height: 180px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.chapter-item {
  height: 30px;
  margin-right: 0;
}
.chapter-empty {
  font-size: 12px;
  color: var(--wt-text-4);
  margin: 4px 0 0;
}
</style>
