<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { ElMessage } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { reviewApi, booksApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { speakWord } from '../../core/speech';

const settings = useSettingsStore();

const serverDate = ref('');
const quota = ref({ limit: 0, used: 0, remaining: 0 });
const summary = ref({ overdue: 0, danger: 0, consolidating: 0 });
const queue = ref<any[]>([]);
const cursor = ref(0);
const typed = ref('');
const choiceMeaning = ref('');
const feedback = ref<any>(null);
const loading = ref(true);

const item = computed(() => queue.value[cursor.value] ?? null);
const form = computed(() => settings.settings?.review_form ?? 'typing');
const showMeaning = computed(() => (settings.settings?.review_show_meaning ?? 1) === 1);

onMounted(async () => {
  await settings.load();
  await refresh();
  loading.value = false;
});

async function refresh() {
  const { data } = await reviewApi.today();
  serverDate.value = data.server_date;
  quota.value = data.quota;
  summary.value = data.summary;
  queue.value = data.queue;
  cursor.value = 0;
  feedback.value = null;
  typed.value = '';
}

async function submitSelf(rating: 'know' | 'vague' | 'unknown') {
  await answer({ word_id: item.value.word_id, form: 'self', rating, reshow: 0, request_id: uuid() });
}

async function submitTyping() {
  await answer({ word_id: item.value.word_id, form: 'typing', typed: typed.value, reshow: 0, duration_ms: 0, request_id: uuid() });
}

async function submitChoice() {
  await answer({ word_id: item.value.word_id, form: 'choice', choice_key: choiceMeaning.value, reshow: 0, request_id: uuid() });
}

async function answer(payload: any) {
  try {
    const { data } = await reviewApi.answer(payload);
    feedback.value = data;
    if (data.reshow_scheduled) ElMessage.info('答错将在本轮队尾重现一次');
  } catch {
    /* toast 已在拦截器处理 */
  }
}

function next() {
  feedback.value = null;
  typed.value = '';
  choiceMeaning.value = '';
  cursor.value += 1;
  if (cursor.value >= queue.value.length) {
    ElMessage.success('今日复习已完成');
    refresh();
  }
}

const tierLabel = (t: number) => (t === 0 ? '逾期' : t === 1 ? '高危' : '巩固中');
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
      <span class="quota">额度 {{ quota.used }} / {{ quota.limit }} · 服务器日期 {{ serverDate }}</span>
    </div>

    <el-card v-if="!item" v-loading="loading" shadow="never" class="q-card">
      <el-empty :description="queue.length ? '复习进行中' : '今日暂无到期复习'" />
    </el-card>

    <el-card v-else shadow="never" class="q-card">
      <div class="q-head">
        <el-tag>第 {{ cursor + 1 }} / {{ queue.length }} 题</el-tag>
        <el-tag :type="item.tier === 0 ? 'danger' : item.tier === 1 ? 'warning' : 'success'">{{ tierLabel(item.tier) }}</el-tag>
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
        <el-input v-model="typed" size="large" placeholder="输入拼写后回车" @keyup.enter="submitTyping" />
      </template>

      <!-- 自评 -->
      <template v-else-if="form === 'self' && !feedback">
        <p class="word-spelling">{{ item.spelling }}</p>
        <p class="word-phonetic" v-if="item.phonetic">{{ item.phonetic }}</p>
        <p class="hint">回想该词的释义，然后自评掌握程度</p>
        <div class="rate-row">
          <el-button type="success" size="large" @click="submitSelf('know')">认识</el-button>
          <el-button type="warning" size="large" @click="submitSelf('vague')">模糊</el-button>
          <el-button type="danger" size="large" @click="submitSelf('unknown')">不认识</el-button>
        </div>
      </template>

      <!-- 选择 -->
      <template v-else-if="form === 'choice' && !feedback">
        <p class="word-spelling">{{ item.spelling }}</p>
        <p class="word-phonetic" v-if="item.phonetic">{{ item.phonetic }}</p>
        <p class="hint">回忆并输入该词的正确释义关键词</p>
        <el-input v-model="choiceMeaning" size="large" placeholder="输入该词的正确释义关键词" @keyup.enter="submitChoice" />
      </template>

      <div v-if="feedback" class="fb" :class="feedback.result">
        <p v-if="item" class="fb-word">
          {{ item.spelling }} <span class="word-phonetic" v-if="item.phonetic">{{ item.phonetic }}</span> · {{ item.meaning }}
        </p>
        <p>
          {{ feedback.result === 'correct' ? '答对 ✔' : feedback.result === 'near' ? '近似正确（间隔减半）' : '答错 ✘' }}
        </p>
        <p class="sm2">
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
        <el-button type="primary" size="large" @click="next">下一题</el-button>
      </div>
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
.quota {
  font-size: 13px;
  color: var(--wt-text-3);
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
}
.word-spelling {
  font-size: 30px;
  font-weight: 700;
  text-align: center;
  margin: 24px 0 4px;
  color: var(--wt-text);
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
</style>
