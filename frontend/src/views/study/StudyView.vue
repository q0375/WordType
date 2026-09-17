<script setup lang="ts">
import { ref, computed, watch, onMounted, nextTick } from 'vue';
import { useRoute } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { booksApi, studyApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { speakWord } from '../../core/speech';
import { uuid } from '../../core/idempotency';
import { TypingSession } from '../../core/typing';

const route = useRoute();
const settings = useSettingsStore();

const books = ref<any[]>([]);
const bookId = ref<number | null>(null);
const chapters = ref<any[]>([]);
const chapterId = ref<number | null>(null);

const serverDate = ref('');
const quota = ref({ used: 0, limit: 20 });
const position = ref<any>(null);
const continueAvailable = ref(false);

const word = ref<any>(null);
const wordIndex = ref(0);
const wordId = ref<number | null>(null);
const scope = computed(() => (chapterId.value ? `chapter:${chapterId.value}` : bookId.value ? `book:${bookId.value}` : undefined));

// 阶段：learn 自评卡 → show 默写展示期 → type 默写输入 → feedback
const phase = ref<'idle' | 'learn' | 'show' | 'type' | 'feedback'>('idle');
const showSeconds = computed(() => settings.settings?.dictation_show_seconds ?? 3);
const looseMatch = computed(() => !!settings.settings?.loose_match);
const typed = ref('');
const typing = ref<TypingSession | null>(null);
const feedback = ref<{ result: string; reshow: boolean; proficiency: number; next?: string } | null>(null);
const reshowCount = ref(0);
const reshowQueue = ref<number[]>([]);
const learnAgainQueue = ref<number[]>([]);
const typingBox = ref<HTMLElement>();

watch(phase, (p) => {
  if (p === 'type') nextTick(() => typingBox.value?.focus());
});

onMounted(async () => {
  await settings.load();
  const { data } = await booksApi.list('all');
  books.value = data.items;
  const preset = route.query.book ? Number(route.query.book) : null;
  if (preset) {
    bookId.value = preset;
    await pickBook(preset);
  }
});

async function pickBook(id: number) {
  bookId.value = id;
  chapterId.value = null;
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
}

async function startStudy() {
  if (!scope.value) {
    ElMessage.warning('请先选择词库/章节');
    return;
  }
  const { data } = await studyApi.next(scope.value);
  serverDate.value = data.server_date;
  quota.value = data.quota;
  position.value = data.position;
  continueAvailable.value = data.continue_available;

  if (data.continue_available) {
    try {
      await ElMessageBox.confirm('检测到上次学习位置，是否继续？', '续学', {
        confirmButtonText: '继续上次',
        cancelButtonText: '重来',
        type: 'info',
      });
    } catch {
      if (position.value) {
        await studyApi.savePosition(position.value.chapter_id, 0);
      }
    }
  }
  loadNext();
}

async function loadNext() {
  // 当轮重刷队列优先（不认识 → 重刷一次，同词最多 2 次）
  const nextId = learnAgainQueue.value.shift() ?? reshowQueue.value.shift();
  if (nextId) {
    wordId.value = nextId;
    phase.value = 'learn';
    return;
  }
  const { data } = await studyApi.next(scope.value);
  quota.value = data.quota;
  word.value = data.next_word;
  if (!data.next_word) {
    phase.value = 'idle';
    if (data.quota.used >= data.quota.limit) ElMessage.warning('今日新词已达上限，去复习巩固一下吧');
    return;
  }
  wordId.value = data.next_word.id;
  wordIndex.value = data.next_word.index;
  reshowCount.value = 0;
  phase.value = 'learn';
  if (settings.settings?.tts_on !== false) speakWord(data.next_word.spelling); // 出卡自动发音（可在设置关闭）
}

async function selfRate(rating: 'know' | 'vague' | 'unknown') {
  if (!wordId.value) return;
  const { data } = await studyApi.selfRate(wordId.value, rating, uuid());
  if (rating === 'unknown' && reshowCount.value < 2) {
    reshowCount.value += 1;
    learnAgainQueue.value.push(wordId.value);
  }
  // 自评后进入默写卡（D17）
  await startDictationCard();
}

async function startDictationCard() {
  phase.value = 'show';
  await nextTick();
  setTimeout(() => {
    if (phase.value === 'show') {
      phase.value = 'type';
      typed.value = '';
      typing.value = new TypingSession(word.value.spelling);
    }
  }, showSeconds.value * 1000);
}

function skipShow() {
  if (phase.value === 'show') {
    phase.value = 'type';
    typed.value = '';
    typing.value = new TypingSession(word.value.spelling);
  }
}

function onKeydown(e: KeyboardEvent) {
  if (phase.value !== 'type') return;
  if (e.key === 'Enter') return;
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
  const { data } = await studyApi.dictation(wordId.value, typed.value, duration, detail, uuid());
  feedback.value = { result: data.result, reshow: data.reshow, proficiency: data.proficiency, next: data.next_review_at };
  phase.value = 'feedback';
  if (data.reshow && reshowCount.value < 2) {
    reshowCount.value += 1;
    reshowQueue.value.push(wordId.value);
  }
  if (position.value) {
    studyApi.savePosition(position.value.chapter_id, wordIndex.value + 1).catch(() => undefined);
  }
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
    <div class="scope-bar">
      <el-select v-model="bookId" placeholder="选择词库" style="width: 220px" @change="pickBook">
        <el-option v-for="b in books" :key="b.id" :label="b.name" :value="b.id" />
      </el-select>
      <el-select v-model="chapterId" placeholder="整本词库" clearable style="width: 220px">
        <el-option v-for="c in chapters" :key="c.id" :label="c.name" :value="c.id" />
      </el-select>
      <el-button type="primary" @click="startStudy">开始 / 继续</el-button>
      <span class="quota" v-if="quota.limit">
        今日新词 {{ quota.used }} / {{ quota.limit }}
      </span>
    </div>

    <el-card v-if="phase === 'idle' && !word" shadow="never" class="empty-card">
      <el-empty description="选择范围后开始学习" />
    </el-card>

    <!-- 自评卡 -->
    <el-card v-if="phase === 'learn' && word" shadow="never" class="word-card">
      <div class="spelling-row">
        <h2 class="spelling">{{ word.spelling }}</h2>
        <el-button circle text @click="speak"><el-icon><VideoPlay /></el-icon></el-button>
      </div>
      <p class="phonetic">{{ word.phonetic }}</p>
      <p class="meaning">{{ word.meaning }}</p>
      <p class="example" v-if="word.example">{{ word.example }}</p>
      <div class="rate-row">
        <el-button type="success" size="large" @click="selfRate('know')">认识</el-button>
        <el-button type="warning" size="large" @click="selfRate('vague')">模糊</el-button>
        <el-button type="danger" size="large" @click="selfRate('unknown')">不认识</el-button>
      </div>
    </el-card>

    <!-- 默写展示期 -->
    <el-card v-if="phase === 'show' && word" shadow="never" class="word-card dictation" @click="skipShow">
      <p class="hint">默写卡：先记住单词（{{ showSeconds }}s），点击可跳过</p>
      <h2 class="spelling">{{ word.spelling }}</h2>
      <p class="meaning">{{ word.meaning }}</p>
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
        <el-button type="primary" size="large" @click="submitDictation">提交默写</el-button>
      </div>
    </el-card>

    <!-- 反馈 -->
    <el-card v-if="phase === 'feedback' && feedback && word" shadow="never" class="word-card">
      <el-result
        :icon="feedback.result === 'correct' ? 'success' : feedback.result === 'near' ? 'warning' : 'error'"
        :title="feedback.result === 'correct' ? '完全正确' : feedback.result === 'near' ? '近似正确（差一个字母）' : '答错了'"
        :sub-title="`正确拼写：${word.spelling} · 熟练度 ${feedback.proficiency}`"
      />
      <div class="rate-row">
        <el-button type="primary" size="large" @click="nextWord">下一个</el-button>
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
.dictation .spelling {
  font-size: 44px;
  color: var(--wt-primary);
}
.phonetic {
  color: var(--wt-text-3);
  margin: 4px 0;
}
.meaning {
  font-size: 18px;
  color: var(--wt-text);
  margin: 12px 0;
}
.example {
  color: var(--wt-text-3);
  font-style: italic;
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
