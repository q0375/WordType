<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onActivated, onBeforeUnmount } from 'vue';
import { partialRatio } from '../../core/judge';
import { useRoute, onBeforeRouteLeave } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { booksApi, studyApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { speakWord } from '../../core/speech';
import { isForbiddenKey } from '../../core/anticheat';
import { confirmAction } from '../../core/confirm';
import { uuid } from '../../core/idempotency';
import { TypingSession } from '../../core/typing';

const route = useRoute();
const settings = useSettingsStore();

const books = ref<any[]>([]);
const bookId = ref<number | null>(null);
const chapters = ref<any[]>([]);
const chapterId = ref<number | null>(null);

const serverDate = ref('');
const todayNew = ref(0);
const position = ref<any>(null);
const continueAvailable = ref(false);

const word = ref<any>(null);
const wordIndex = ref(0);
const restudyNotified = ref(false);
const wordId = ref<number | null>(null);
const scope = computed(() => (chapterId.value ? `chapter:${chapterId.value}` : bookId.value ? `book:${bookId.value}` : undefined));

// 阶段：learn 自评卡 → type 默写输入 → feedback → completed 全部学完
// （原 show 展示期已按用户要求移除：模糊/不认识自评后直接进默写输入）
const phase = ref<'idle' | 'learn' | 'type' | 'feedback' | 'completed'>('idle');
const restudyMode = ref(false); // 重学模式：用户在完成页主动选择「重学一遍」后开启
const restudySeen = ref<Set<number>>(new Set()); // 本轮重学已出现过的词；重复出现即一轮完成
const lastScopeTotal = ref(0);
const typed = ref('');
const typing = ref<TypingSession | null>(null);
const feedback = ref<{ result: string; reshow: boolean; proficiency: number; next?: string; ratio?: number } | null>(null);

// 展示用得分比：优先后端值；旧后端缺 ratio 时按同源 difflib 算法本地兜底（2026-09-22 修复显示恒 0%）
function ratioOf(data: any, typedSent: string): number {
  if (typeof data?.ratio === 'number') return data.ratio;
  return partialRatio(typedSent || '', word.value?.spelling || '');
}
const reshowCount = ref(0);
const reshowQueue = ref<number[]>([]); // 仅默写「答错」时重现（后端 reshow），同词最多 2 次；拼对直接进下一个
const typingBox = ref<HTMLElement>();

// 键盘优先：自评 1/2/3 · 默写回车提交 · 反馈回车下一个
function onGlobalKey(e: KeyboardEvent) {
  if (phase.value === 'idle' || (phase.value !== 'completed' && !word.value)) return;
  if (e.repeat) return; // 长按回车会连发，自评/下一个都是离散动作，重复事件一律忽略
  if (document.querySelector('.el-overlay-message-box')) return;
  const active = document.activeElement as HTMLElement | null;
  // 只有焦点落在卡片自身的按钮上（认识/提交/下一个）才让回车交给按钮，避免一次回车两遍动作；
  // 顶部「开始/继续」按钮保留焦点时回车仍走键盘流
  if (document.querySelector('.el-overlay-message-box')) return; // 确认弹窗打开时交给弹窗
  // Esc = 结束本次学习（等价于点「结束本次学习」，同样走确认）
  if (e.key === 'Escape' && phase.value !== 'idle' && phase.value !== 'completed') {
    e.preventDefault();
    void endSession();
    return;
  }
  if (e.key === 'Enter' && active?.closest('.word-card')) return;
  if (phase.value === 'learn') {
    if (e.key === '1' || e.key === 'Enter') {
      // 阻止回车二次激活焦点所在的「开始/继续」按钮（否则 loadNext 直接换词）
      if (e.key === 'Enter') e.preventDefault();
      selfRate('know');
    } else if (e.key === '2') selfRate('vague');
    else if (e.key === '3') selfRate('unknown');
    return;
  }
  if (phase.value === 'feedback' && e.key === 'Enter') {
    e.preventDefault();
    nextWord();
  }
  if (phase.value === 'completed' && e.key === 'Enter') {
    e.preventDefault();
    restartUnit();
  }
  // type 阶段的输入与提交由默写输入框自身的 onKeydown 处理
}

watch(phase, (p) => {
  if (p === 'type') nextTick(() => typingBox.value?.focus());
});

onMounted(async () => {
  window.addEventListener('keydown', onGlobalKey);
  await settings.load();
  await loadBooks();
  const preset = route.query.book ? Number(route.query.book) : null;
  if (preset) {
    bookId.value = preset;
    await pickBook(preset);
  }
});

// 词库列表全量拉取（page_size 上限 500，避免词库超过默认分页 20 条后私有库被截断看不到）
async function loadBooks() {
  const { data } = await booksApi.list('all', '', 1, 500);
  books.value = data.items;
}

async function pickBook(id: number) {
  bookId.value = id;
  chapterId.value = null;
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
}

// keep-alive 激活：处理从仪表盘带 ?book= 跳入的预选（仅空闲时应用，不打断进行中的会话）；
// 同时刷新词库列表——否则先开本页、再去词库页新建/导入的私有库不会出现在下拉里
onActivated(() => {
  loadBooks();
  const preset = route.query.book ? Number(route.query.book) : null;
    if (preset && preset !== bookId.value && phase.value === 'idle') pickBook(preset);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKey);
});

// 中途结束本次学习：已学/已默写的词均已实时入库，只重置本地会话
function resetSession() {
  phase.value = 'idle';
  word.value = null;
  wordId.value = null;
  typed.value = '';
  typing.value = null;
  feedback.value = null;
  reshowCount.value = 0;
  reshowQueue.value = [];
  restudyMode.value = false;
  restudySeen.value = new Set();
}

async function endSession() {
  // 结束属于不可逆动作（已学词已入库、未学词要重新排队）：一律先确认（2026-10-03）
  const ok = await confirmAction({
    title: '结束学习',
    message: '确定结束本次学习？已完成的词均已记录，未学的词下次再来。',
    confirmText: '结束学习',
    cancelText: '继续学习',
  });
  if (!ok) return;
  resetSession();
  ElMessage.info({ message: '本次学习已结束，已完成的词均已记录', duration: 2000, showClose: true });
}

// 离开学习模块：学习中（learn/type/feedback）必须先决定——结束本次学习，或留下继续；
// 已完成（completed）无需询问，但一律清空现场，回来时回到范围选择页而非「学完了」页。
onBeforeRouteLeave(async () => {
  if (phase.value === 'idle') return true;
  if (phase.value !== 'completed') {
    const ok = await confirmAction({
      title: '结束学习',
      message: '当前还有正在进行的学习，离开前是否结束本次学习？',
      confirmText: '结束学习',
      cancelText: '继续学习',
    });
    if (!ok) return false; // 留在本页继续学
  }
  resetSession();
  return true;
});

// 请求失败（单词已被删除、网络中断等）时不能把卡片卡在中间态：退回范围选择，用户可重新开始
function failSession(e: unknown) {
  resetSession();
  ElMessage.error({ message: `${e instanceof Error ? e.message : '学习请求失败'}，本次学习已中止`, duration: 2000, showClose: true });
}

async function startStudy() {
  // 会话进行中不允许重复开始（2026-09-21）：此前再点会重新拉词/弹续学框，打断当前词
  if (phase.value !== 'idle') return;
  if (!scope.value) {
    ElMessage.warning({ message: '请先选择词库/章节', duration: 2000, showClose: true });
    return;
  }
  restudyNotified.value = false;
  let data: any;
  try {
    data = (await studyApi.next(scope.value)).data;
  } catch (e) {
    failSession(e);
    return;
  }
  serverDate.value = data.server_date;
  todayNew.value = data.today_new;
  position.value = data.position;
  continueAvailable.value = data.continue_available;
  lastScopeTotal.value = data.scope_total ?? 0;

  // 已全部学过时没有"上次位置"可续，直接走完成页
  if (data.continue_available && !data.all_learned) {
    try {
      await ElMessageBox.confirm('检测到上次学习位置，是否继续？', '续学', {
        confirmButtonText: '继续上次',
        cancelButtonText: '重来',
        type: 'info',
      });
    } catch {
      if (position.value) {
        await studyApi.savePosition(position.value.chapter_id, 0).catch(() => undefined);
      }
    }
  }
  loadNext();
}

async function loadNext() {
  // 当轮重刷队列优先：仅默写「答错」的词会重现（同词最多 2 次），拼对一律进下一个
  const nextId = reshowQueue.value.shift();
  if (nextId) {
    wordId.value = nextId;
    phase.value = 'learn';
    return;
  }
  let data: any;
  try {
    data = (await studyApi.next(scope.value, restudyMode.value, restudyMode.value ? [...restudySeen.value] : [])).data;
  } catch (e) {
    failSession(e);
    return;
  }
  todayNew.value = data.today_new;
  lastScopeTotal.value = data.scope_total ?? lastScopeTotal.value;
  if (!data.next_word) {
    word.value = null;
    wordId.value = null;
    if (data.all_learned || data.restudy_round_done) {
      // 全部学过：不再自动重学；重学模式下排除完所有词=本轮重学完成。
      // 交给用户决定（重学一遍 / 换其他单元）
      phase.value = 'completed';
      return;
    }
    phase.value = 'idle';
    ElMessage.warning({
      message: '该范围内没有单词，请先导入单词或换一个单元',
      duration: 2000,
      showClose: true,
    });
    return;
  }
  if (restudyMode.value) {
    restudySeen.value.add(data.next_word.id);
  }
  word.value = data.next_word;
  if (data.next_word.restudy && !restudyNotified.value) {
    restudyNotified.value = true;
    ElMessage.info({ message: '进入重学模式：再学一遍巩固记忆', duration: 2000, showClose: true });
  }
  wordId.value = data.next_word.id;
  wordIndex.value = data.next_word.index;
  reshowCount.value = 0;
  phase.value = 'learn';
  if (settings.settings?.tts_on !== false) speakWord(data.next_word.spelling); // 出卡自动发音（可在设置关闭）
}

// 完成页选择「重学一遍」：强制进入重学循环，一轮结束后再次回到完成页
async function restartUnit() {
  restudyMode.value = true;
  restudySeen.value = new Set();
  restudyNotified.value = false;
  feedback.value = null;
  await loadNext();
}

const ratingBusy = ref(false);

async function selfRate(rating: 'know' | 'vague' | 'unknown') {
  if (!wordId.value || ratingBusy.value) return;
  ratingBusy.value = true;
  try {
    await studyApi.selfRate(wordId.value, rating, uuid());
  } catch (e) {
    failSession(e);
    return;
  } finally {
    ratingBusy.value = false;
  }
  // 自评只落进度：认识 → 直接下一个；模糊 / 不认识 → 进默写，
  // 默写「拼对」即通过进下一个，「答错」才重现巩固（不因自评结果预排队）
  if (rating === 'know') {
    await loadNext();
    return;
  }
  await startDictationCard();
}

function startDictationCard() {
  phase.value = 'type';
  typed.value = '';
  typing.value = new TypingSession(word.value.spelling);
}

function onKeydown(e: KeyboardEvent) {
  if (phase.value !== 'type') return;
  if (isForbiddenKey(e)) {
    // 防作弊（D37）：Ctrl/Alt/Insert 等键按下即清空输入
    typed.value = '';
    ElMessage.warning({ message: '检测到禁用按键（Ctrl/Alt/Insert），输入已清空', duration: 2000, showClose: true });
    return;
  }
  if (e.key === 'Enter') {
    if (e.repeat) return; // 长按回车不得连交
    submitDictation();
    return;
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

async function submitDictation() {
  if (!wordId.value || !word.value) return;
  const detail = typing.value?.detail();
  const duration = detail?.duration_ms ?? 0;
  let data: any;
  try {
    data = (await studyApi.dictation(wordId.value, typed.value, duration, detail, uuid())).data;
  } catch (e) {
    failSession(e);
    return;
  }
  feedback.value = { result: data.result, reshow: data.reshow, proficiency: data.proficiency, next: data.next_review_at, ratio: ratioOf(data, typed.value) };
  if (data.reshow && reshowCount.value < 2) {
    reshowCount.value += 1;
    reshowQueue.value.push(wordId.value);
  }
  if (position.value) {
    studyApi.savePosition(position.value.chapter_id, wordIndex.value + 1).catch(() => undefined);
  }
  // 完全正确：不打断节奏，直接进下一个词；近似 / 错误才弹反馈页
  if (data.result === 'correct') {
    feedback.value = null;
    await loadNext();
    return;
  }
  phase.value = 'feedback';
}

function nextWord() {
  feedback.value = null;
  loadNext();
}

function speak() {
  if (!word.value) return;
  speakWord(word.value.spelling);
}
</script>

<template>
  <PageShell title="学习模式" subtitle="卡片自评 + 默写">
    <template #actions>
      <el-button v-if="phase !== 'idle' && phase !== 'completed'" type="danger" plain @click="endSession">结束本次学习</el-button>
    </template>
    <div class="scope-bar">
      <el-select v-model="bookId" placeholder="选择词库" style="width: 220px" :disabled="phase !== 'idle'" @change="pickBook">
        <el-option v-for="b in books" :key="b.id" :label="b.name" :value="b.id">
          <div class="book-opt">
            <span>{{ b.name }}</span>
            <span class="book-tag" :class="b.is_public ? 'pub' : 'pri'">{{ b.is_public ? '公共' : '私有' }}</span>
          </div>
        </el-option>
      </el-select>
      <el-select v-model="chapterId" placeholder="整本词库" clearable style="width: 220px" :disabled="phase !== 'idle'">
        <el-option v-for="c in chapters" :key="c.id" :label="c.name" :value="c.id" />
      </el-select>
      <!-- 会话进行中禁用：开始后按钮锁定，须先「结束本次学习」才能再开（2026-09-21） -->
      <el-button type="primary" :disabled="phase !== 'idle'" @click="startStudy">开始 / 继续</el-button>
      <span class="quota" v-if="phase !== 'idle'">今日新词 {{ todayNew }}</span>
    </div>

    <el-card v-if="phase === 'idle' && !word" shadow="never" class="empty-card">
      <el-empty description="选择范围后开始学习" />
    </el-card>

    <!-- 自评卡 -->
    <el-card v-if="phase === 'learn' && word" shadow="never" class="word-card">
      <div class="spelling-row">
        <h2 class="spelling">{{ word.spelling }}</h2>
        <el-tag v-if="word.restudy" type="warning" size="small">重学</el-tag>
        <el-button circle text @click="speak"><el-icon><VideoPlay /></el-icon></el-button>
      </div>
      <p class="phonetic" v-if="word.phonetic">{{ word.phonetic }}</p>
      <p class="meaning">{{ word.meaning }}</p>
      <p class="example" v-if="word.example">{{ word.example }}</p>
      <div class="rate-row">
        <el-button type="success" size="large" @click="selfRate('know')">认识 <kbd>1</kbd></el-button>
        <el-button type="warning" size="large" @click="selfRate('vague')">模糊 <kbd>2</kbd></el-button>
        <el-button type="danger" size="large" @click="selfRate('unknown')">不认识 <kbd>3</kbd></el-button>
      </div>
      <p class="hint">按「认识」直接学下一个；「模糊 / 不认识」进入默写，答对进下一个、答错才重现巩固</p>
    </el-card>

    <!-- 默写输入 -->
    <el-card v-if="phase === 'type' && word" shadow="never" class="word-card">
      <p class="hint">根据释义默写单词：</p>
      <p class="meaning">{{ word.meaning }}</p>
      <div class="typing-box" tabindex="0" @keydown.prevent="onKeydown" ref="typingBox">
        <span class="chars">
          <span
            v-for="(ch, i) in word.spelling"
            :key="i"
            class="char"
            :class="{ ok: typed[i] === ch, bad: typed[i] && typed[i] !== ch }"
          >{{ typed[i] || '' }}</span>
        </span>
      </div>
      <div class="rate-row">
        <el-button type="primary" size="large" @click="submitDictation">提交默写 <kbd>↵</kbd></el-button>
      </div>
    </el-card>

    <!-- 反馈：非完全正确统一展示得分比（2026-09-21 口径统一，不再区分「近似/答错」话术） -->
    <el-card v-if="phase === 'feedback' && feedback && word" shadow="never" class="word-card">
      <el-result
        :icon="feedback.result === 'correct' ? 'success' : 'warning'"
        :title="feedback.result === 'correct' ? '完全正确' : `不完全正确（得分比 ${Math.round((feedback.ratio ?? 0) * 100)}%）`"
        :sub-title="`正确拼写：${word.spelling} · 熟练度 ${feedback.proficiency}`"
      />
      <div class="rate-row">
        <el-button type="primary" size="large" @click="nextWord">下一个 <kbd>↵</kbd></el-button>
      </div>
    </el-card>

    <!-- 完成：范围内全部学过，由用户决定重学或换单元 -->
    <el-card v-if="phase === 'completed'" shadow="never" class="word-card">
      <el-result
        icon="success"
        title="本单元已学完"
        :sub-title="restudyMode ? '本轮重学已完成，可以再学一遍巩固，或换其他单元' : `该范围共 ${lastScopeTotal} 个单词都已学过，可以重学一遍或换其他单元`"
      />
      <div class="rate-row">
        <el-button type="primary" size="large" @click="restartUnit">重学一遍 <kbd>↵</kbd></el-button>
        <el-button size="large" @click="resetSession">换其他单元</el-button>
      </div>
    </el-card>
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
.quota {
  font-size: 13px;
  color: var(--wt-text-3);
}
.book-opt {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.book-tag {
  font-size: 11px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 4px;
}
.book-tag.pri {
  color: #b45309;
  background: #fef3c7;
}
.book-tag.pub {
  color: var(--wt-text-4, #999);
  border: 1px solid var(--wt-border, #ddd);
}
kbd {
  display: inline-block;
  min-width: 18px;
  padding: 0 4px;
  border: 1px solid var(--wt-border);
  border-bottom-width: 2px;
  border-radius: 4px;
  background: var(--wt-card, transparent);
  font-size: 12px;
  line-height: 16px;
  text-align: center;
  margin-left: 4px;
}
.word-card {
  max-width: 640px;
  margin: 0 auto;
  text-align: center;
}
.empty-card {
  max-width: 640px;
  margin: 0 auto;
}
.spelling-row {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
}
.spelling {
  font-size: 36px;
  margin: 8px 0;
  color: var(--wt-text);
}
.phonetic {
  color: var(--wt-text-3);
  margin: 4px 0;
}
.meaning {
  font-size: 18px;
  color: var(--wt-text);
  margin: 12px 0;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.example {
  color: var(--wt-text-3);
  font-style: italic;
  word-break: break-word;
  overflow-wrap: anywhere;
}
.hint {
  color: var(--wt-text-4);
  font-size: 13px;
}
.rate-row {
  margin-top: 20px;
  display: flex;
  justify-content: center;
  gap: 12px;
}
.typing-box {
  margin: 16px auto;
  padding: 16px;
  border: 2px solid var(--wt-border);
  border-radius: 8px;
  min-height: 64px;
  outline: none;
  cursor: text;
}
.typing-box:focus {
  border-color: var(--wt-primary);
}
.char {
  display: inline-block;
  width: 22px;
  height: 32px;
  line-height: 32px;
  margin: 0 2px;
  border-bottom: 2px solid var(--wt-border-strong);
  font-size: 20px;
  font-family: Consolas, monospace;
}
.char.ok {
  color: var(--wt-text);
  border-color: var(--wt-success);
}
.char.bad {
  color: var(--wt-danger);
  border-color: var(--wt-danger);
}
</style>
