<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, watch, nextTick } from 'vue';
import { ElMessage } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { reviewApi, booksApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { speakWord } from '../../core/speech';

const settings = useSettingsStore();

const serverDate = ref('');
const summary = ref({ overdue: 0, danger: 0, consolidating: 0 });
const queue = ref<any[]>([]);
const cursor = ref(0);
const typed = ref('');
const choiceMeaning = ref('');
const feedback = ref<any>(null);
const loading = ref(true);
const ended = ref(false);
const inputRef = ref<any>(null);

/** 出题/切题/回到输入态时自动聚焦输入框（typing/choice 模式）；同时重置打字计时 */
watch([cursor, feedback, queue], () => {
  firstKeyTs = 0;
  lastKeyTs = 0;
  nextTick(() => {
    if (!feedback.value && item.value && (form.value === 'typing' || form.value === 'choice')) {
      inputRef.value?.focus();
    }
  });
});

const item = computed(() => queue.value[cursor.value] ?? null);
const form = computed(() => settings.settings?.review_form ?? 'typing');
const showMeaning = computed(() => (settings.settings?.review_show_meaning ?? 1) === 1);

onMounted(async () => {
  await settings.load();
  await refresh();
  loading.value = false;
  loadPlan();
});

async function refresh() {
  const { data } = await reviewApi.today();
  serverDate.value = data.server_date;
  summary.value = data.summary;
  queue.value = data.queue;
  cursor.value = 0;
  feedback.value = null;
  typed.value = '';
  ended.value = false;
}

// reshow=1：答错后队尾重现的题，服务端按 is_reshow 匹配待答行，必须原样传回
const itemReshow = computed(() => (item.value?.reshow ? 1 : 0));
const submitting = ref(false);

// 每词打字计时（2026-09-23）：口径与学习/练习一致 = 首键→末键；此前恒传 0，统计 WPM 缺复习数据
let firstKeyTs = 0;
let lastKeyTs = 0;
function onInputKeydown() {
  if (!firstKeyTs) firstKeyTs = performance.now();
  lastKeyTs = performance.now();
}

async function submitSelf(rating: 'know' | 'vague' | 'unknown') {
  await answer({ word_id: item.value.word_id, form: 'self', rating, reshow: itemReshow.value, request_id: uuid() });
}

async function submitTyping() {
  await answer({ word_id: item.value.word_id, form: 'typing', typed: typed.value, reshow: itemReshow.value, duration_ms: firstKeyTs && lastKeyTs ? Math.round(lastKeyTs - firstKeyTs) : 0, request_id: uuid() });
}

async function submitChoice() {
  await answer({ word_id: item.value.word_id, form: 'choice', choice_key: choiceMeaning.value, reshow: itemReshow.value, request_id: uuid() });
}

async function answer(payload: any) {
  if (submitting.value) return;
  submitting.value = true;
  try {
    const { data } = await reviewApi.answer(payload);
    feedback.value = data;
    if (data.reshow_scheduled) ElMessage.info({ message: '答错将在本轮队尾重现一次', duration: 2000, showClose: true });
  } catch {
    /* toast 已在拦截器处理 */
  } finally {
    submitting.value = false;
  }
}

function next() {
  feedback.value = null;
  typed.value = '';
  choiceMeaning.value = '';
  cursor.value += 1;
  if (cursor.value >= queue.value.length) {
    ElMessage.success({ message: '今日复习已完成', duration: 2000, showClose: true });
    refresh();
    loadPlan(); // SM-2 已推进，未来排期有变化
  }
}

/** 全局键盘：feedback 阶段回车=下一题；自评模式 1/2/3 评分、回车=认识 */
function onKey(e: KeyboardEvent) {
  if (!item.value || submitting.value) return;
  if (e.repeat) return; // 长按回车会连跳复习项
  if (feedback.value) {
    if (e.key === 'Enter') {
      e.preventDefault();
      next();
    }
    return;
  }
  if (form.value === 'self') {
    if (e.key === '1' || e.key === 'Enter') {
      e.preventDefault();
      submitSelf('know');
    } else if (e.key === '2') {
      e.preventDefault();
      submitSelf('vague');
    } else if (e.key === '3') {
      e.preventDefault();
      submitSelf('unknown');
    }
  }
}

onMounted(() => window.addEventListener('keydown', onKey));
onBeforeUnmount(() => window.removeEventListener('keydown', onKey));

const tierLabel = (t: number) => (t === 0 ? '逾期' : t === 1 ? '高危' : '巩固中');

// ---------- 未来复习计划 ----------
const plan = ref<any[]>([]);
const planLoading = ref(false);
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

const dayLabel = (d: string) => {
  const dt = new Date(d + 'T00:00:00');
  const diff = Math.round((dt.getTime() - new Date(serverDate.value + 'T00:00:00').getTime()) / 86400000);
  const prefix = diff === 1 ? '明天' : diff === 2 ? '后天' : '';
  return `${prefix ? prefix + ' ' : ''}${d} ${WEEK[dt.getDay()]}`;
};

// ---------- 提前复习（2026-10-04）：把未来排期词移出计划、置顶到当前游标之后 ----------
/** 本会话已置顶的词（题头打「提前复习」标签） */
const advancedIds = ref<Set<number>>(new Set());
/** 已提前复习的日期：计划里不再重复显示（作答后 SM-2 排到新日期时会重新出现） */
const advancedDates = ref<Set<string>>(new Set());

const planEmptyText = computed(() =>
  advancedDates.value.size
    ? '未来 14 天的排期已全部提前复习，词都已进入上方队列'
    : '未来 14 天暂无排期（新词学习后会自动排进来）'
);

async function loadPlan() {
  planLoading.value = true;
  try {
    const { data } = await reviewApi.plan(14);
    // 本会话已「提前复习」的日期不再重复出现（词已进队列；作答后 SM-2 排到新日期时会重新出现）
    plan.value = (data.plan || []).filter((g: any) => !advancedDates.value.has(g.date));
  } finally {
    planLoading.value = false;
  }
}

/** 计划里移除该日期分组（词已进队列，不应再留在未来排期里） */
function removePlanDate(date: string) {
  advancedDates.value = new Set([...advancedDates.value, date]);
  plan.value = plan.value.filter((g: any) => g.date !== date);
}

async function advanceToTop(date: string) {
  const { data } = await reviewApi.advance(date);
  const ids: number[] = data.word_ids || [];
  if (!ids.length) {
    // 已提前复习过（本次会话内已置顶）或该批词已作答完：直接收起该日期，不再提示错误
    if (advancedDates.value.has(date)) {
      removePlanDate(date);
      ElMessage.info({ message: '该日期的词已在当前队列中，无需重复置顶', duration: 2000, showClose: true });
    } else {
      ElMessage.info({ message: '该日期的词今日队列中已无待答题（可能已复习过）', duration: 2000, showClose: true });
    }
    return;
  }
  // 反馈阶段点提前复习：把当前已答的题并入「已答区」，避免重排后当前词被换掉、反馈对不上题
  const base = feedback.value ? cursor.value + 1 : cursor.value;
  const ahead = queue.value.slice(0, base); // 已答过的保持原位
  const rest = queue.value.slice(base);
  const s = new Set(ids);
  queue.value = [...ahead, ...rest.filter((q: any) => s.has(q.word_id)), ...rest.filter((q: any) => !s.has(q.word_id))];
  cursor.value = base;
  advancedIds.value = new Set([...advancedIds.value, ...ids]);
  removePlanDate(date);
  ElMessage.success({ message: `${ids.length} 个词已进入复习队列，第 ${cursor.value + 1} 题开始作答`, duration: 2500, showClose: true });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

</script>

<template>
  <PageShell title="复习模式" subtitle="SM-2 间隔重复 · 逾期优先">
    <template #actions>
      <el-button @click="refresh">刷新队列</el-button>
    </template>

    <div class="summary-row">
      <el-tag type="danger">逾期 {{ summary.overdue }}</el-tag>
      <el-tag type="warning">高危 {{ summary.danger }}</el-tag>
      <el-tag type="success">巩固中 {{ summary.consolidating }}</el-tag>
    </div>

    <el-card v-if="!item" v-loading="loading" shadow="never" class="q-card">
      <el-empty :description="ended ? '本次复习已结束，已作答的题目均已记录' : queue.length ? '复习进行中' : '还没有学过的单词，先去学习吧'" />
    </el-card>

    <el-card v-else shadow="never" class="q-card">
      <div class="q-head">
        <el-tag>第 {{ cursor + 1 }} / {{ queue.length }} 题</el-tag>
        <el-tag :type="item.tier === 0 ? 'danger' : item.tier === 1 ? 'warning' : 'success'">{{ tierLabel(item.tier) }}</el-tag>
        <el-tag v-if="advancedIds.has(item.word_id)" type="primary">提前复习</el-tag>
        <el-tag v-if="item.overdue_days" type="info">逾期 {{ item.overdue_days }} 天</el-tag>
        <el-button v-if="item.spelling" circle size="small" @click="speakWord(item.spelling)" title="播放发音">
          <el-icon><VideoPlay /></el-icon>
        </el-button>
      </div>

      <!-- 打字默写 -->
      <template v-if="form === 'typing' && !feedback">
        <template v-if="showMeaning">
          <p class="word-meaning">{{ item.meaning || '（该词暂无释义）' }}</p>
          <p class="word-phonetic" v-if="item.phonetic">{{ item.phonetic }}</p>
          <p class="hint">看释义，默写英文拼写（释义显示可在设置中关闭）</p>
        </template>
        <template v-else>
          <p class="prompt">听发音，默写该词的英文拼写（释义提示已关闭）</p>
        </template>
        <el-input ref="inputRef" v-model="typed" v-anticheat size="large" placeholder="输入拼写后回车（禁止 Ctrl/Alt/Insert）" @keydown="onInputKeydown" @keydown.enter.prevent="submitTyping" />
      </template>

      <!-- 自评 -->
      <template v-else-if="form === 'self' && !feedback">
        <p class="word-spelling">{{ item.spelling }}</p>
        <p class="word-phonetic" v-if="item.phonetic">{{ item.phonetic }}</p>
        <p class="hint">回想该词的释义，然后自评掌握程度</p>
        <div class="rate-row">
          <el-button type="success" size="large" @click="submitSelf('know')">认识 (1)</el-button>
          <el-button type="warning" size="large" @click="submitSelf('vague')">模糊 (2)</el-button>
          <el-button type="danger" size="large" @click="submitSelf('unknown')">不认识 (3)</el-button>
        </div>
        <p class="hint">键盘：1/2/3 评分，回车 = 认识</p>
      </template>

      <!-- 选择 -->
      <template v-else-if="form === 'choice' && !feedback">
        <p class="word-spelling">{{ item.spelling }}</p>
        <p class="word-phonetic" v-if="item.phonetic">{{ item.phonetic }}</p>
        <p class="hint">回忆并输入该词的正确释义关键词</p>
        <el-input ref="inputRef" v-model="choiceMeaning" v-anticheat size="large" placeholder="输入该词的正确释义关键词（禁止 Ctrl/Alt/Insert）" @keydown.enter.prevent="submitChoice" />
      </template>

      <div v-if="feedback" class="fb" :class="feedback.result">
        <p v-if="item" class="fb-word">
          {{ item.spelling }} <span class="word-phonetic" v-if="item.phonetic">{{ item.phonetic }}</span> · {{ item.meaning }}
        </p>
        <p>
          {{ feedback.result === 'correct' ? '答对 ✔' : feedback.result === 'near' ? '近似正确（间隔减半）' : '答错 ✘' }}
        </p>
        <!-- 毕业词（答题前 p≥80 无排期）任何结果都不显示 SM-2 反馈；普通词答对毕业（next=NULL）同样隐藏 -->
        <p class="sm2" v-if="feedback.sm2?.next_review_at && !feedback.was_graduated">
          SM-2：间隔 {{ feedback.sm2?.interval_days }} 天 · ease {{ feedback.sm2?.ease_factor }} · 下次复习 {{ feedback.sm2?.next_review_at }} · 熟练度 {{ feedback.proficiency }}
        </p>
      </div>

      <div class="foot-row" v-if="!feedback && form === 'typing'">
        <el-button type="primary" size="large" @click="submitTyping">提交</el-button>
      </div>
      <div class="foot-row" v-if="!feedback && form === 'choice'">
        <el-button type="primary" size="large" @click="submitChoice">提交</el-button>
      </div>
      <div class="foot-row" v-if="feedback">
        <el-button type="primary" size="large" @click="next">下一题 ↵</el-button>
      </div>
    </el-card>

    <!-- 未来复习计划（SM-2 排期，可提前复习） -->
    <el-card shadow="never" class="plan-card" v-loading="planLoading">
      <template #header>
        <div class="plan-head">
          <span>未来复习计划（SM-2 自动排期）</span>
          <el-button size="small" @click="loadPlan">刷新计划</el-button>
        </div>
      </template>
      <p class="plan-hint">今日队列已包含全部学过的词（无上限，逾期优先）；下方是 SM-2 为各词安排的未来复习日期。点「提前复习」会把该日期的词移出计划并立刻置顶到当前队列游标之后，马上开始作答。</p>
      <el-empty v-if="!plan.length" :description="planEmptyText" :image-size="80" />
      <el-collapse v-else>
        <el-collapse-item v-for="g in plan" :key="g.date">
          <template #title>
            <span class="plan-day">{{ dayLabel(g.date) }}</span>
            <el-tag size="small" type="info">{{ g.count }} 词</el-tag>
            <el-button size="small" type="primary" plain style="margin-left: 12px" @click.stop="advanceToTop(g.date)">提前复习</el-button>
          </template>
          <div class="plan-words">
            <el-tooltip v-for="w in g.words" :key="w.id" :content="w.meaning" placement="top" :show-after="150">
              <span class="plan-word">{{ w.spelling }}</span>
            </el-tooltip>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-card>
  </PageShell>
</template>

<style scoped>
.summary-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  flex-wrap: wrap;
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
  font-size: 18px;
  text-align: center;
  margin: 24px 0;
  color: var(--wt-text);
}
.word-meaning {
  font-size: 24px;
  font-weight: 600;
  text-align: center;
  margin: 24px 0 4px;
  color: var(--wt-text);
  word-break: break-word;
  overflow-wrap: anywhere;
}
.word-spelling {
  font-size: 30px;
  font-weight: 700;
  text-align: center;
  margin: 24px 0 4px;
  color: var(--wt-text);
  word-break: break-word;
  overflow-wrap: anywhere;
}
.word-phonetic {
  text-align: center;
  color: var(--wt-text-3);
  margin: 0 0 4px;
  font-size: 14px;
}
.rate-row {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin: 20px 0;
}
.fb {
  text-align: center;
  margin: 16px 0;
  font-weight: 600;
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
.sm2 {
  font-size: 12px;
  color: var(--wt-text-3);
  font-weight: 400;
}
.foot-row {
  display: flex;
  justify-content: center;
  margin-top: 12px;
}
.hint {
  font-size: 12px;
  color: var(--wt-text-4);
}
.plan-card {
  max-width: 640px;
  margin: 16px auto 0;
}
.plan-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.plan-hint {
  font-size: 12px;
  color: var(--wt-text-4);
  margin: 0 0 8px;
}
.plan-day {
  margin-right: 8px;
  font-weight: 600;
}
.plan-words {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.plan-word {
  font-size: 13px;
  padding: 2px 8px;
  background: var(--wt-bg-2, #f5f7fa);
  border-radius: 4px;
  color: var(--wt-text, #303133);
}
</style>
