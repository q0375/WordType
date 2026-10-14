<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { confirmAction } from '../../core/confirm';
import { booksApi, examApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { speakWord } from '../../core/speech';
import { splitSentence } from '../../core/text';

const settings = useSettingsStore();

type Phase = 'config' | 'paper' | 'result' | 'records';
const phase = ref<Phase>('config');
const books = ref<any[]>([]);
const bookId = ref<number | null>(null);
const chapters = ref<any[]>([]);
const chapterIds = ref<number[]>([]); // 多章节复选（2026-09-22）

// 已选章节（可多选，2026-09-22）合计词数变化后按合计预填一套合法题量，避免默认 5+3 超过小章节词数
watch(chapterIds, (ids) => {
  const n = chapters.value.filter((c) => ids.includes(c.id)).reduce((a, c) => a + (c.word_count || 0), 0);
  if (n <= 0) {
    cfg.value = { dictation: 0, choice: 0, cloze: 0, listening: 0 };
    return;
  }
  const d = Math.min(5, n);
  const ch = n - d >= 3 ? 3 : Math.max(0, n - d);
  cfg.value = { dictation: d, choice: ch, cloze: 0, listening: 0 };
});

const cfg = ref({ dictation: 0, choice: 0, cloze: 0, listening: 0 });
const timeLimit = ref(20);
const passScore = ref(60);
// 每词限时档位（2026-09-21 恢复）：宽松 ×1.5 / 标准 ×1.0 / 紧凑 ×0.7，只改每题倒计时；
// 计分恒按原始时限（选哪档都不影响卷面分），倍数由后端下发，前端不参与计算。
type TimePolicy = 'relaxed' | 'standard' | 'tight';
const timePolicy = ref<TimePolicy>('standard');

// 题型行（从上到下竖排 + 每行 max 按钮）：按下 max → 该题型占满整章词数，其余题型归零
type QType = 'dictation' | 'choice' | 'cloze' | 'listening';
const typeRows: ReadonlyArray<{ key: QType; label: string }> = [
  { key: 'dictation', label: '默写' },
  { key: 'choice', label: '选择' },
  { key: 'cloze', label: '挖空' },
  { key: 'listening', label: '听音' },
];

function maxOnlyType(k: QType) {
  const n = chapterWordCount.value;
  if (!n) {
    ElMessage.warning({ message: '请先选择章节，max 按该章节实际词数计算', duration: 2000, showClose: true });
    return;
  }
  const next: Record<QType, number> = { dictation: 0, choice: 0, cloze: 0, listening: 0 };
  next[k] = n;
  cfg.value = next;
}

const paper = ref<any>(null);
const answers = ref<Record<string, { typed?: string; choice_key?: string }>>({});
const remaining = ref(0); // 整卷剩余秒数（兜底）
const elapsedMs = ref(0); // 已用毫秒（用于历史成绩「用时」）
const blurCount = ref(0);
const submitting = ref(false);
const result = ref<any>(null);
// 防泄题（2026-09-23）：管理员发布的考核交卷后不返回题目/答案，成绩单只显示对错与得分比
const resultHasQuestions = computed(() => !!result.value?.per_question?.some((q: any) => q.spelling || q.correct_answer));
const records = ref<any[]>([]);
const recordsTotal = ref(0);
const recordsPage = ref(1);
const RECORDS_PAGE_SIZE = 20;

// ---- 每词限时（D35）：顺序答题，每题独立倒计时 ----
const activeIdx = ref(0);
const answerInput = ref<any>(null);
const qTimerPct = computed(() => {
  const limit = currentQ.value?.payload?.limit_s ?? 60;
  return Math.max(0, Math.min(100, (qRemaining.value / limit) * 100));
});

function focusCurrentInput() {
  nextTick(() => {
    const q = currentQ.value;
    if (q && q.type !== 'choice' && !timedOut.value[q.qid]) answerInput.value?.focus();
  });
}

// 每题切换后自动聚焦输入框，时间不空转
watch(activeIdx, () => focusCurrentInput());

// 键盘驱动：回车=下一题；选择题支持 1-4 / A-D 作答（答题页全局生效）
function onGlobalKey(e: KeyboardEvent) {
  if (phase.value !== 'paper' || submitting.value) return;
  if (document.querySelector('.el-overlay-message-box')) return; // 弹窗打开时交给弹窗
  const tag = (document.activeElement?.tagName || '').toLowerCase();
  if (e.key === 'Enter') {
    if (e.repeat) return; // 长按回车会连跳题直至误交卷
    if (tag === 'button') return; // 焦点在按钮上时交给按钮（放弃/交卷）
    e.preventDefault();
    nextQuestion().catch(() => undefined);
    return;
  }
  const q = currentQ.value;
  if (q?.type === 'choice' && !timedOut.value[q.qid]) {
    const map: Record<string, string> = { '1': 'A', '2': 'B', '3': 'C', '4': 'D', a: 'A', b: 'B', c: 'C', d: 'D' };
    const key = map[e.key.toLowerCase()];
    if (key && q.payload.options.some((o: any) => o.key === key)) {
      setChoice(q.qid, key);
      e.preventDefault();
    }
  }
}
const qStartTs = ref(0);
const qRemaining = ref(0);
const timedOut = ref<Record<string, boolean>>({});

// ---- 考核卷（老师布置） ----
const assigned = ref<any[]>([]);

let timer: number | null = null;
let idemKey = '';
let assignedPoll: number | null = null;

// 选题上限 = 所选章节的实际词数（chapters 接口返回 word_count），未选章节时不限制单项但拦截开局
// 题量上限 = 已选章节合计词数（多章节复选，2026-09-22）
const chapterWordCount = computed(() => chapters.value.filter((c) => chapterIds.value.includes(c.id)).reduce((a, c) => a + (c.word_count || 0), 0));
const typeMax = computed(() => (chapterWordCount.value > 0 ? chapterWordCount.value : 0));
const countOver = computed(() => chapterWordCount.value > 0 && totalQuestions.value > chapterWordCount.value);
const noWords = computed(() => chapterIds.value.length > 0 && chapterWordCount.value === 0);

// 全选/反选（2026-09-22）：下拉头部"全选"复选框，勾上选全部章节，支持半选态
const allChaptersSelected = computed(() => chapters.value.length > 0 && chapterIds.value.length === chapters.value.length);
const someChaptersSelected = computed(() => chapterIds.value.length > 0 && !allChaptersSelected.value);
function toggleAllChapters(v: string | number | boolean) {
  chapterIds.value = v ? chapters.value.map((c) => c.id) : [];
}

const totalQuestions = computed(() => Object.values(cfg.value).reduce((a, b) => a + b, 0));
const mmss = computed(() => {
  const m = Math.floor(remaining.value / 60);
  const s = remaining.value % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
});
const qs = computed<any[]>(() => paper.value?.questions ?? []);
const currentQ = computed(() => qs.value[activeIdx.value] ?? null);

// 挖空题：例句「英文 中文」拆两行（2026-10-04，与练习页同规则）
const examCloze = computed(() => splitSentence(currentQ.value?.payload?.sentence_masked));
const answeredCount = computed(() => Object.values(answers.value).filter((a) => a && (a.typed || a.choice_key)).length);
const isLast = computed(() => activeIdx.value >= qs.value.length - 1);
// 计分口径（2026-09-20）：得分比 = 词序相似度（difflib SequenceMatcher，连续值，区分大小写）；
// 2026-09-20 起每词限时档位取消：每题倒计时与计分权重同源（均为原始时限）。

onMounted(async () => {
  await settings.load();
  const s = settings.settings!;
  timeLimit.value = s.exam_time_limit;
  passScore.value = s.exam_pass_score;
  const { data } = await booksApi.list('all', '', 1, 500);
  books.value = data.items;
  loadRecords();
  loadAssigned();
  assignedPoll = window.setInterval(loadAssigned, 60000); // 考核卷到时自动出现在列表
  window.addEventListener('beforeunload', beforeUnload);
  document.addEventListener('visibilitychange', onBlur);
  window.addEventListener('blur', onWindowBlur);
  window.addEventListener('focus', onWindowFocus);
  document.addEventListener('fullscreenchange', onFsChange);
  window.addEventListener('keydown', onGlobalKey);
  await resumeIfActive();
});

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
  if (assignedPoll) clearInterval(assignedPoll);
  window.removeEventListener('beforeunload', beforeUnload);
  document.removeEventListener('visibilitychange', onBlur);
  window.removeEventListener('blur', onWindowBlur);
  window.removeEventListener('focus', onWindowFocus);
  document.removeEventListener('fullscreenchange', onFsChange);
  if (blurPending) {
    window.clearTimeout(blurPending);
    blurPending = 0;
  }
  exitFullscreenQuiet();
  window.removeEventListener('keydown', onGlobalKey);
});

// 离开考核模块（切换路由）时：进行中的卷子必须先做决定——提前交卷，或留下继续作答；
// 不允许带着未交卷的卷子离开（「不能放着原来的进度」）。
onBeforeRouteLeave(async () => {
  if (phase.value !== 'paper') return true;
  try {
    await ElMessageBox.confirm('当前还有未交卷的考核卷，离开前是否提前交卷？', '提前交卷', {
      confirmButtonText: '提前交卷',
      cancelButtonText: '继续作答',
      type: 'warning',
    });
  } catch {
    return false; // 留在本页继续作答
  }
  await submit();
  return true;
});

function beforeUnload(e: BeforeUnloadEvent) {
  if (phase.value === 'paper') {
    e.preventDefault();
    e.returnValue = '';
  }
}

// ---- 切屏/失焦检测（2026-09-22 强化：双通道 + 回合去重 + 强制全屏）----
// 旧行为只看 visibilitychange(document.hidden)：切标签/最小化/被完全遮挡才计数，
// 分屏下浏览器仍可见、焦点去了别的窗口 → 检测不到；先摆在旁边的作弊窗口只要不点击也检不到。
// 现在：1) window blur——焦点一离开浏览器窗口即计数（分屏点击他窗/Alt-Tab/Win+D 全覆盖）；
//       2) visibilitychange(hidden)——兜底标签页隐藏；同一次"离开回合"（blur+hidden 连袂触发）只计一次，
//          窗口重新拿回焦点才开新回合。
// 强制全屏：开卷即进入全屏，预先摆在旁边的窗口被盖住——要看它必须退全屏或点击它（都会被检出）。
const isFs = ref(false);
const fsSupported = typeof document !== 'undefined' && Boolean(document.documentElement.requestFullscreen);
let blurRound = false;
let blurPending = 0; // 失焦延迟判定：blur 后 600ms 内 focus 回来 = 自愈抖动（全屏切换/浏览器内部焦点转移），不计数

function countBlurOnce() {
  if (phase.value !== 'paper' || blurRound || submitting.value) return;
  blurRound = true;
  blurCount.value += 1;
  examApi.blur(paper.value.paper_id).catch(() => undefined); // fire-and-forget
}

function onWindowBlur() {
  if (blurPending) return;
  blurPending = window.setTimeout(() => {
    blurPending = 0;
    countBlurOnce();
  }, 600);
}

function onWindowFocus() {
  if (blurPending) {
    window.clearTimeout(blurPending);
    blurPending = 0;
  }
  if (!document.hidden) blurRound = false;
}

function onBlur() {
  if (document.hidden) {
    // 标签页隐藏 = 真离开，立即计数（并撤销尚未到期的抖动判定，避免重复）
    if (blurPending) {
      window.clearTimeout(blurPending);
      blurPending = 0;
    }
    countBlurOnce();
  } else if (document.hasFocus()) {
    blurRound = false;
  }
}

function onFsChange() {
  isFs.value = document.fullscreenElement === document.documentElement;
  if (phase.value === 'paper' && fsSupported && !isFs.value) {
    ElMessage.warning({ message: '考核须全屏进行，请点击「回到全屏」继续作答', duration: 3000, showClose: true });
  }
}

function enterFullscreen() {
  if (!fsSupported || isFs.value) return;
  try {
    document.documentElement.requestFullscreen?.()?.catch(() => undefined);
  } catch {
    /* 不支持则静默：仍有切屏检测兜底 */
  }
}

function exitFullscreenQuiet() {
  try {
    if (document.fullscreenElement) {
      document.exitFullscreen?.()?.catch(() => undefined);
    }
  } catch {
    /* ignore */
  }
}

async function resumeIfActive() {
  try {
    const { data } = await examApi.active();
    if (!data || phase.value === 'paper') return;
    try {
      await ElMessageBox.confirm(
        `你有一张未交卷的考核卷（${data.config_snapshot?.template_name ? `考核卷「${data.config_snapshot.template_name}」` : '自组卷'}），剩余 ${Math.ceil(data.remaining_s / 60)} 分钟。`,
        '继续考试？',
        { confirmButtonText: '继续作答', cancelButtonText: '作废该卷', type: 'warning' },
      );
      beginPaper(data, data.remaining_s);
    } catch {
      await examApi.void(data.paper_id).catch(() => undefined);
      ElMessage.info({ message: '已作废上次未完成的考核卷', duration: 2000, showClose: true });
    }
  } catch {
    /* 无进行中卷子 */
  }
}

async function loadAssigned() {
  try {
    const { data } = await examApi.assigned();
    assigned.value = data.items ?? [];
  } catch {
    /* ignore */
  }
}

async function pickBook(id: number) {
  bookId.value = id;
  chapterIds.value = [];
  cfg.value = { dictation: 0, choice: 0, cloze: 0, listening: 0 };
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
}

async function start() {
  if (!chapterIds.value.length) {
    ElMessage.warning({ message: '请选择考核章节（可多选）', duration: 2000, showClose: true });
    return;
  }
  if (noWords.value) {
    ElMessage.warning({ message: '该章节没有单词，请换一个章节', duration: 2000, showClose: true });
    return;
  }
  if (totalQuestions.value <= 0) {
    ElMessage.warning({ message: '题量必须大于 0', duration: 2000, showClose: true });
    return;
  }
  if (countOver.value) {
    ElMessage.warning({
      message: `题目总数 ${totalQuestions.value} 超过所选章节合计词数 ${chapterWordCount.value}，请减少题量`,
      duration: 2000,
      showClose: true,
    });
    return;
  }
  const type_counts: Record<string, number> = {};
  for (const [k, v] of Object.entries(cfg.value)) if (v > 0) type_counts[k] = v;
  try {
    const { data } = await examApi.start({
      chapter_ids: chapterIds.value,
      type_counts,
      time_limit_min: timeLimit.value,
      pass_score: passScore.value,
      time_policy: timePolicy.value,
    });
    beginPaper(data, timeLimit.value * 60);
  } catch {
    /* EXAM_ALREADY_ACTIVE 等由拦截器提示 */
  }
}

async function startAssigned(t: any) {
  try {
    const { data } = await examApi.startAssigned(t.template_id);
    beginPaper(data, t.time_limit_min ? t.time_limit_min * 60 : 3600);
  } catch {
    /* 拦截器提示 */
  }
}

function beginPaper(data: any, remainingSec: number) {
  paper.value = data;
  const init: Record<string, { typed?: string; choice_key?: string }> = {};
  for (const q of data.questions) init[q.qid] = {};
  answers.value = init;
  timedOut.value = {};
  blurCount.value = 0;
  blurRound = false;
  if (blurPending) {
    window.clearTimeout(blurPending);
    blurPending = 0;
  }
  elapsedMs.value = 0; // 开新卷必须清零：此前跨卷累加，第 N 卷的「用时」= 前 N-1 卷之和 + 本卷（2026-09-20 修复）
  result.value = null;
  phase.value = 'paper';
  enterFullscreen(); // 强制全屏（2026-09-22）：开卷即全屏，盖住预先摆好的旁窗；退出会被检出并提示回到全屏
  remaining.value = remainingSec;
  activeIdx.value = 0;
  startQuestionTimer();
  focusCurrentInput();
  idemKey = uuid();
  if (timer) clearInterval(timer);
  timer = window.setInterval(tick, 1000);
}

function startQuestionTimer() {
  const q = currentQ.value;
  qStartTs.value = Date.now();
  qRemaining.value = q?.payload?.limit_s ?? 60;
}

function gotoQuestion(idx: number) {
  // 只进不退：顺序作答，答过的题不能返回修改
  if (idx <= activeIdx.value || idx >= qs.value.length) return;
  activeIdx.value = idx;
  startQuestionTimer();
  focusCurrentInput();
}

async function nextQuestion() {
  if (isLast.value) {
    await submit();
    return;
  }
  gotoQuestion(activeIdx.value + 1);
}

function tick() {
  remaining.value -= 1;
  elapsedMs.value += 1000;
  // 每题倒计时
  const elapsed = Math.floor((Date.now() - qStartTs.value) / 1000);
  qRemaining.value = Math.max(0, (currentQ.value?.payload?.limit_s ?? 60) - elapsed);
  if (qRemaining.value <= 0 && currentQ.value && !timedOut.value[currentQ.value.qid]) {
    timedOut.value[currentQ.value.qid] = true;
    ElMessage.warning({ message: '本题时间到，已自动跳至下一题', duration: 2000, showClose: true });
    nextQuestion().catch(() => undefined);
    return;
  }
  if (remaining.value <= 0) submit();
}

function setChoice(qid: string, key: string) {
  answers.value[qid] = { ...(answers.value[qid] || {}), choice_key: key };
  (document.activeElement as HTMLElement | null)?.blur(); // 焦点让位，回车直达「下一题」
}

function onTyped(qid: string, v: string) {
  answers.value[qid] = { ...(answers.value[qid] || {}), typed: v };
}

function qState(q: any): 'pending' | 'current' | 'done' | 'timeout' {
  if (timedOut.value[q.qid]) return 'timeout';
  const a = answers.value[q.qid];
  if (a && (a.typed || a.choice_key)) return 'done';
  if (qs.value[activeIdx.value]?.qid === q.qid) return 'current';
  return 'pending';
}

async function submit() {
  if (submitting.value || phase.value !== 'paper') return;
  // 交卷不可撤销：一律先确认，并明示未作答题的去向（2026-10-03）
  const unanswered = qs.value.length - Object.keys(answers.value).length;
  const ok = await confirmAction({
    title: '交卷',
    message: unanswered > 0
      ? `还有 ${unanswered} 题未作答（按 0 分计，且不计入错题本），确定交卷？交卷后不可修改。`
      : '确定交卷？交卷后不可再修改答案。',
    confirmText: '交卷',
    cancelText: '继续作答',
  });
  if (!ok) return;
  submitting.value = true;
  if (timer) clearInterval(timer);
  const answerList = Object.entries(answers.value).map(([qid, a]) => ({ qid, ...a }));
  // 交卷失败重试 ≤3 次（同幂等键），仍失败 = 作废（口径22）
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const { data } = await examApi.submit(paper.value.paper_id, answerList, elapsedMs.value, idemKey);
      result.value = data;
      phase.value = 'result';
      exitFullscreenQuiet(); // 已交卷，还屏桌面
      submitting.value = false;
      loadAssigned(); // 成绩状态刷新
      return;
    } catch {
      /* retry */
    }
  }
  await examApi.void(paper.value.paper_id).catch(() => undefined);
  submitting.value = false;
  ElMessage.error({ message: '网络中断，本卷作废', duration: 2000, showClose: true });
  phase.value = 'config';
  exitFullscreenQuiet();
}

async function abandon() {
  const ok = await confirmAction({
    title: '放弃考核',
    message: '放弃将不计成绩，确定作废本卷？',
    confirmText: '放弃本卷',
    cancelText: '继续作答',
  });
  if (!ok) return;
  if (timer) clearInterval(timer);
  await examApi.void(paper.value.paper_id).catch(() => undefined);
  phase.value = 'config';
  exitFullscreenQuiet();
  loadAssigned();
}

async function loadRecords() {
  const { data } = await examApi.records(recordsPage.value);
  records.value = data.items;
  recordsTotal.value = data.total;
}

function onRecordsPageChange(p: number) {
  recordsPage.value = p;
  loadRecords();
}

function showRecords() {
  recordsPage.value = 1;
  loadRecords();
  phase.value = 'records';
}

const typeLabel: Record<string, string> = { dictation: '默写', choice: '选择', cloze: '挖空', listening: '听音' };
const resultLabel = (r: string) => ({ correct: '正确', near: '近似', wrong: '错误' })[r] ?? r;
// 成绩来源：template=管理员下发卷 / self=自组卷（兼容旧数据：无 source 时看有无 template_name）
const isAssigned = (row: any) => (row.source ? row.source === 'template' : !!row.config_snapshot?.template_name);
function typeCountsText(tc: Record<string, number> | undefined) {
  const parts = Object.entries(tc || {})
    .filter(([, n]) => Number(n) > 0)
    .map(([k, n]) => `${typeLabel[k] ?? k} ${n}`);
  return parts.join(' + ') || '—';
}
</script>

<template>
  <PageShell title="考核模式" subtitle="限时组卷 · 按词计时 · 服务端计分">
    <template #actions>
      <el-button @click="showRecords">历史成绩</el-button>
    </template>

    <!-- 组卷配置 -->
    <el-card v-if="phase === 'config'" shadow="never" class="q-card">
      <!-- 老师布置的考核卷 -->
      <template v-if="assigned.length">
        <el-divider content-position="left">待参加的考核卷</el-divider>
        <div v-for="t in assigned" :key="t.template_id" class="assigned-item">
          <div class="assigned-info">
            <b>{{ t.name }}</b>
            <span class="assigned-meta">共 {{ t.question_count }} 题 · 整卷 {{ t.time_limit_min }} 分钟</span>
            <span v-if="t.expires_at" class="assigned-meta">截止：{{ t.expires_at }}</span>
          </div>
          <el-tag v-if="t.attempt" type="success" size="small">已考 {{ t.attempt.score }} 分</el-tag>
          <el-tag v-if="t.max_attempts" :type="t.attempts_used >= t.max_attempts ? 'danger' : 'info'" size="small">
            {{ t.attempts_used }}/{{ t.max_attempts }} 次
          </el-tag>
          <el-tag v-if="t.max_attempts && t.attempts_used >= t.max_attempts && !t.active_paper_id" type="warning" size="small">次数已用完</el-tag>
          <el-button
            type="primary"
            :disabled="!!t.max_attempts && t.attempts_used >= t.max_attempts && !t.active_paper_id"
            @click="startAssigned(t)"
          >
            {{ t.active_paper_id ? '继续作答' : t.attempt ? '再考一次' : '开始考核' }}
          </el-button>
        </div>
        <el-divider content-position="left">自主组卷练习</el-divider>
      </template>

      <el-form label-width="110px">
        <el-form-item label="词库">
          <el-select v-model="bookId" style="width: 240px" placeholder="选择词库" @change="pickBook">
            <el-option v-for="b in books" :key="b.id" :label="b.name" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="章节">
          <el-select v-model="chapterIds" multiple collapse-tags collapse-tags-tooltip style="width: 240px" placeholder="选择章节（可多选）">
            <template #header>
              <el-checkbox :model-value="allChaptersSelected" :indeterminate="someChaptersSelected" @change="toggleAllChapters">全选</el-checkbox>
            </template>
            <el-option v-for="c in chapters" :key="c.id" :label="`${c.name}（${c.word_count} 词）`" :value="c.id" />
          </el-select>
          <span v-if="chapterWordCount" class="hint">已选章节合计 {{ chapterWordCount }} 词，选题总数不能超过它</span>
        </el-form-item>
        <el-form-item label="题型 × 题量">
          <div class="tc-col">
            <div v-for="t in typeRows" :key="t.key" class="tc-line">
              <el-input-number v-model="cfg[t.key]" :min="0" :max="typeMax" />
              <span class="tc-label">{{ t.label }}</span>
              <el-button size="small" @click="maxOnlyType(t.key)">max</el-button>
            </div>
          </div>
          <p v-if="!chapterIds.length" class="field-hint">请先选择章节（可多选），题量上限按所选章节合计词数自动计算</p>
          <p v-else-if="noWords" class="field-hint field-hint--warn">所选章节没有单词，无法组卷</p>
          <p v-else-if="countOver" class="field-hint field-hint--warn">
            题目总数 {{ totalQuestions }} 已超过所选章节合计词数 {{ chapterWordCount }}
          </p>
          <p v-else class="field-hint">max：该题型占满所选章节合计词数（{{ chapterWordCount }} 题），其余题型自动归零</p>
        </el-form-item>
        <el-form-item label="整卷限时">
          <el-input-number v-model="timeLimit" :min="5" :max="120" />
          <span class="hint">分钟（超过整卷时间自动交卷）</span>
        </el-form-item>
        <el-form-item label="每词限时">
          <el-radio-group v-model="timePolicy">
            <el-radio-button value="relaxed">宽松</el-radio-button>
            <el-radio-button value="standard">标准</el-radio-button>
            <el-radio-button value="tight">紧凑</el-radio-button>
          </el-radio-group>
          <span class="hint">宽松 ×1.5 · 标准 ×1.0 · 紧凑 ×0.7 —— 只改变每题倒计时，不影响得分</span>
        </el-form-item>
        <el-form-item label="及格线">
          <el-input-number v-model="passScore" :min="0" :max="100" />
        </el-form-item>
        <!-- 宽松判定开关已于 2026-09-19 取消：得分比为连续值（2026-09-20 起按序列相似度计），无开关可配 -->
        <p class="field-hint">
          每题倒计时按词长自动计算（短词短、长词长），可再用上面的限时档位整体放宽/收紧；
          计分口径：每题得分比 = 词序相似度（连续计分，区分大小写、空格参与比较）；<br />
          卷面分 = Σ(得分比 × 原始时限) ÷ Σ(原始时限) × 100 —— 权重恒用原始时限，
          选「宽松 / 紧凑」只影响答题时间，<b>不改变得分</b>。
        </p>
        <el-button type="primary" size="large" :disabled="!chapterIds.length || noWords || countOver" @click="start">
          开始考核（共 {{ totalQuestions }} 题）
        </el-button>
      </el-form>
    </el-card>

    <!-- 答题：顺序模式 + 每题独立倒计时 -->
    <el-card v-if="phase === 'paper' && currentQ" shadow="never" class="q-card">
      <div class="paper-head">
        <div class="q-timer" :class="{ danger: qRemaining <= 5 }">
          <span class="q-timer-num">{{ qRemaining }}</span><span class="q-timer-unit">秒</span>
          <div class="q-timer-bar"><div class="q-timer-fill" :style="{ width: qTimerPct + '%' }" /></div>
        </div>
        <el-tag type="danger" size="large" effect="dark">⏱ 整卷 {{ mmss }}</el-tag>
        <el-tag>失焦 {{ blurCount }} 次</el-tag>
        <el-tag v-if="fsSupported && !isFs" type="warning" effect="dark">未全屏</el-tag>
        <el-button v-if="fsSupported && !isFs" size="small" type="primary" @click="enterFullscreen">回到全屏</el-button>
        <el-tag type="info">已答 {{ answeredCount }} / {{ qs.length }}</el-tag>
        <el-button type="warning" text @click="abandon">放弃本卷</el-button>
      </div>

      <!-- 题号状态：顺序作答不可跳题，仅作进度展示 -->
      <div class="q-nav">
        <span v-for="(q, i) in qs" :key="q.qid" class="q-dot" :class="`q-dot--${qState(q)}`" :title="typeLabel[q.type]">
          {{ i + 1 }}
        </span>
        <span class="q-nav-legend">灰=未答 · 蓝=当前 · 绿=已答 · 划线=超时</span>
      </div>

      <div class="q-item">
        <p class="q-title">
          {{ activeIdx + 1 }}. {{ currentQ.type === 'dictation' ? '默写：' : currentQ.type === 'choice' ? '选择：' : currentQ.type === 'listening' ? '听音拼写：' : '例句挖空：' }}
          <template v-if="currentQ.type === 'dictation'">{{ currentQ.payload.meaning }}</template>
          <template v-else-if="currentQ.type === 'cloze'">{{ examCloze.en }}</template>
          <template v-else-if="currentQ.type === 'listening'">
            播放发音后拼写
            <el-button v-if="currentQ.payload.spelling" circle size="small" style="margin-left: 8px" @click="speakWord(currentQ.payload.spelling)" title="播放发音">
              <el-icon><VideoPlay /></el-icon>
            </el-button>
          </template>
          <template v-else>{{ currentQ.payload.phonetic }}</template>
        </p>
        <p v-if="currentQ.type === 'cloze' && examCloze.cn" class="q-title-cn">{{ examCloze.cn }}</p>
        <div v-if="currentQ.type === 'choice'" class="options">
          <el-button
            v-for="o in currentQ.payload.options"
            :key="o.key"
            :type="answers[currentQ.qid]?.choice_key === o.key ? 'primary' : 'default'"
            size="large"
            @click="setChoice(currentQ.qid, o.key)"
          >
            {{ o.key }}. {{ o.text }}
          </el-button>
        </div>
        <el-input
          v-else
          :key="currentQ.qid"
          ref="answerInput"
          v-anticheat
          :model-value="answers[currentQ.qid]?.typed ?? ''"
          size="large"
          :disabled="timedOut[currentQ.qid]"
          placeholder="输入答案后回车（禁止粘贴与 Ctrl/Alt/Insert）"
          @paste.prevent
          @update:model-value="(v: string) => onTyped(currentQ.qid, v)"
        />
      </div>

      <div class="foot-row">
        <el-button v-if="!isLast" type="warning" size="large" @click="nextQuestion">下一题 →</el-button>
        <el-button type="primary" size="large" :loading="submitting" @click="submit">
          {{ isLast ? '交卷' : '答完了，提前交卷' }}
        </el-button>
      </div>
      <p class="foot-hint">考核按顺序作答，进入下一题后<b>不能返回修改</b> · 回车=下一题<template v-if="currentQ.type === 'choice'"> · 按 1-4 或 A-D 选择</template></p>
    </el-card>

    <!-- 成绩单 -->
    <el-card v-if="phase === 'result' && result" shadow="never" class="q-card q-card--result">
      <el-result
        :icon="result.passed ? 'success' : 'warning'"
        :title="`${result.score} 分`"
        :sub-title="`加权得分比 ${(result.correct_rate * 100).toFixed(1)}% · 用时 ${result.duration}s · 失焦 ${result.blur_count} 次`"
      />
      <el-table :data="result.per_question" size="small">
        <el-table-column prop="qid" label="题号" width="60">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column label="题型" width="70">
          <template #default="{ row }">{{ typeLabel[row.type] || row.type }}</template>
        </el-table-column>
        <!-- 防泄题（2026-09-23）：管理员发布的考核不返回题目/答案，这三列只在自组卷时渲染 -->
        <el-table-column v-if="resultHasQuestions" label="题目" min-width="200">
          <template #default="{ row }">
            <b>{{ row.spelling }}</b>
            <span class="q-meaning">{{ row.meaning }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="resultHasQuestions" label="我的作答" min-width="140">
          <template #default="{ row }">
            <span :class="row.result === 'correct' ? 'ans-ok' : 'ans-bad'">{{ row.your_answer || '（未作答）' }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="resultHasQuestions" label="正确答案" min-width="140">
          <template #default="{ row }">{{ row.correct_answer }}</template>
        </el-table-column>
        <el-table-column prop="limit_s" label="权重时限" width="80">
          <template #default="{ row }">{{ row.limit_s ?? '-' }}s</template>
        </el-table-column>
        <el-table-column label="判定" width="150">
          <template #default="{ row }">
            <el-tag :type="row.result === 'correct' ? 'success' : row.result === 'near' ? 'warning' : 'danger'">
              {{ resultLabel(row.result) }}（得分比 {{ Math.round((row.raw_score || 0) * 100) }}%）
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="加权贡献" width="100">
          <template #default="{ row }">{{ (((row.raw_score || 0) * (row.limit_s || 0)) as number).toFixed(1) }}</template>
        </el-table-column>
      </el-table>
      <p class="foot-hint">
        卷面分 = 各题「得分比 × 原始时限」之和 ÷ 全部题目原始时限之和 × 100（长题权重更高）。
      </p>
      <div class="foot-row">
        <el-button type="primary" @click="phase = 'config'">再来一卷</el-button>
        <el-button @click="showRecords">历史成绩</el-button>
      </div>
    </el-card>

    <!-- 历史成绩 -->
    <el-card v-if="phase === 'records'" shadow="never" class="q-card">
      <el-table :data="records" size="small">
        <el-table-column prop="created_at" label="时间" width="170" />
        <el-table-column label="来源" width="120">
          <template #default="{ row }">
            <el-tag v-if="isAssigned(row)" size="small" type="primary">管理员考核卷</el-tag>
            <el-tag v-else size="small" type="info">自组卷</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="score" label="分数" width="80" />
        <el-table-column prop="duration" label="用时(s)" width="80" />
        <el-table-column prop="blur_count" label="失焦" width="70" />
        <el-table-column label="配置">
          <template #default="{ row }">
            <template v-if="isAssigned(row)">考核卷「{{ row.config_snapshot.template_name }}」/ {{ row.config_snapshot.time_limit_min }}min</template>
            <template v-else>自组卷 · {{ typeCountsText(row.config_snapshot.type_counts) }} / {{ row.config_snapshot.time_limit_min }}min</template>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 12px; display: flex; justify-content: flex-end">
        <el-pagination
          v-model:current-page="recordsPage"
          :page-size="RECORDS_PAGE_SIZE"
          :total="recordsTotal"
          layout="total, prev, pager, next"
          background
          hide-on-single-page
          @current-change="onRecordsPageChange"
        />
      </div>
      <div class="foot-row">
        <el-button @click="phase = 'config'">返回</el-button>
      </div>
    </el-card>
  </PageShell>
</template>

<style scoped>
.q-card {
  max-width: 760px;
  margin: 0 auto;
}
.q-card--result {
  max-width: 980px;
}
.q-meaning {
  margin-left: 8px;
  font-size: 12px;
  color: var(--wt-text-3);
}
.ans-ok {
  color: var(--wt-success);
}
.ans-bad {
  color: var(--wt-danger);
}
.assigned-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--wt-border);
  border-radius: 8px;
  margin-bottom: 10px;
}
.assigned-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.assigned-meta {
  font-size: 12px;
  color: var(--wt-text-3);
}
.tc-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tc-line {
  display: flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
}
.tc-label {
  min-width: 32px;
  color: var(--wt-text-3);
}
.tc-col .el-input-number {
  width: 130px;
}
.hint {
  font-size: 12px;
  color: var(--wt-text-4);
  margin-left: 8px;
}
.field-hint {
  width: 100%;
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--wt-text-4);
}
.field-hint--warn {
  color: var(--wt-danger);
}
.paper-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.q-nav {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.q-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid var(--wt-border);
  background: var(--wt-card);
  color: var(--wt-text-2);
  font-size: 12px;
  cursor: default;
  user-select: none;
}
.q-dot--current {
  border-color: var(--wt-primary);
  color: var(--wt-primary);
  font-weight: 700;
}
.q-dot--done {
  background: var(--wt-primary-bg);
  color: var(--wt-primary);
}
.q-dot--timeout {
  color: var(--wt-text-4);
  text-decoration: line-through;
}
.q-nav-legend {
  font-size: 12px;
  color: var(--wt-text-4);
  align-self: center;
  margin-left: 6px;
}
.q-item {
  margin-bottom: 18px;
  min-height: 120px;
}
.q-title {
  font-size: 20px;
  font-weight: 600;
  line-height: 1.6;
  color: var(--wt-text);
  margin: 0 0 14px;
  padding: 10px 14px;
  word-break: break-word;
  overflow-wrap: anywhere;
  background: var(--wt-card-2, rgba(0, 0, 0, 0.02));
  border-left: 4px solid var(--wt-primary);
  border-radius: 6px;
}
/* 挖空题第二行：例句中文翻译（2026-10-04），弱化、与英文题干同容器风格 */
.q-title-cn {
  font-size: 15px;
  font-weight: 400;
  line-height: 1.5;
  color: var(--wt-text-4);
  margin: -8px 0 14px;
  padding: 0 14px 0 18px;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.q-timer {
  position: relative;
  min-width: 110px;
  padding: 4px 12px 7px;
  border: 2px solid var(--wt-primary);
  border-radius: 8px;
  text-align: center;
  background: var(--wt-primary-bg);
  overflow: hidden;
}
.q-timer-num {
  font-size: 26px;
  font-weight: 800;
  font-family: Consolas, monospace;
  color: var(--wt-primary);
}
.q-timer-unit {
  font-size: 12px;
  color: var(--wt-text-3);
  margin-left: 2px;
}
.q-timer-bar {
  position: absolute;
  left: 0;
  bottom: 0;
  width: 100%;
  height: 4px;
}
.q-timer-fill {
  height: 100%;
  background: var(--wt-primary);
  transition: width 1s linear;
}
.q-timer.danger {
  border-color: #f56c6c;
  animation: q-pulse 1s infinite;
}
.q-timer.danger .q-timer-num {
  color: #f56c6c;
}
.q-timer.danger .q-timer-fill {
  background: #f56c6c;
}
@keyframes q-pulse {
  50% {
    box-shadow: 0 0 0 5px rgba(245, 108, 108, 0.22);
  }
}
.options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.options .el-button {
  height: auto;
  min-height: 44px;
  white-space: normal;
  text-align: left;
  justify-content: flex-start;
  word-break: break-word;
  overflow-wrap: anywhere;
  padding-top: 6px;
  padding-bottom: 6px;
  line-height: 1.5;
}
.foot-row {
  display: flex;
  justify-content: center;
  margin-top: 16px;
  gap: 12px;
}
.foot-hint {
  text-align: center;
  color: var(--wt-text-4);
  font-size: 13px;
  margin: 10px 0 0;
}
</style>
