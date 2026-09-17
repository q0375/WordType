<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { booksApi, examApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { speakWord } from '../../core/speech';

const settings = useSettingsStore();

type Phase = 'config' | 'paper' | 'result' | 'records';
const phase = ref<Phase>('config');
const books = ref<any[]>([]);
const bookId = ref<number | null>(null);
const chapters = ref<any[]>([]);
const chapterId = ref<number | null>(null);

const cfg = ref({ dictation: 5, choice: 3, cloze: 0, listening: 0 });
const timeLimit = ref(20);
const passScore = ref(60);
const loose = ref(false);

const paper = ref<any>(null);
const answers = ref<Record<string, { typed?: string; choice_key?: string }>>({});
const remaining = ref(0);
const blurCount = ref(0);
const submitting = ref(false);
const result = ref<any>(null);
const records = ref<any[]>([]);
const recordsTotal = ref(0);

let timer: number | null = null;
let idemKey = '';

const totalQuestions = computed(() => Object.values(cfg.value).reduce((a, b) => a + b, 0));
const mmss = computed(() => {
  const m = Math.floor(remaining.value / 60);
  const s = remaining.value % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
});

onMounted(async () => {
  await settings.load();
  const s = settings.settings!;
  timeLimit.value = s.exam_time_limit;
  passScore.value = s.exam_pass_score;
  loose.value = !!s.exam_loose_match;
  const { data } = await booksApi.list('all');
  books.value = data.items;
  loadRecords();
  window.addEventListener('beforeunload', beforeUnload);
  document.addEventListener('visibilitychange', onBlur);
});

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
  window.removeEventListener('beforeunload', beforeUnload);
  document.removeEventListener('visibilitychange', onBlur);
});

function beforeUnload(e: BeforeUnloadEvent) {
  if (phase.value === 'paper') {
    e.preventDefault();
    e.returnValue = '';
  }
}

function onBlur() {
  if (phase.value === 'paper' && document.hidden) {
    blurCount.value += 1;
    examApi.blur(paper.value.paper_id).catch(() => undefined); // fire-and-forget
  }
}

async function pickBook(id: number) {
  bookId.value = id;
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
}

async function start() {
  if (!chapterId.value) {
    ElMessage.warning('请选择考核章节');
    return;
  }
  if (totalQuestions.value <= 0) {
    ElMessage.warning('题量必须大于 0');
    return;
  }
  const type_counts: Record<string, number> = {};
  for (const [k, v] of Object.entries(cfg.value)) if (v > 0) type_counts[k] = v;
  try {
    const { data } = await examApi.start({
      chapter_id: chapterId.value,
      type_counts,
      time_limit_min: timeLimit.value,
      pass_score: passScore.value,
      loose_match: loose.value,
    });
    paper.value = data;
    // 为每题预置答案对象，避免 v-model 绑到 undefined
    const init: Record<string, { typed?: string; choice_key?: string }> = {};
    for (const q of data.questions) init[q.qid] = {};
    answers.value = init;
    blurCount.value = 0;
    result.value = null;
    phase.value = 'paper';
    remaining.value = timeLimit.value * 60;
    idemKey = uuid();
    timer = window.setInterval(tick, 1000);
  } catch {
    /* EXAM_ALREADY_ACTIVE 等由拦截器提示 */
  }
}

function tick() {
  remaining.value -= 1;
  if (remaining.value <= 0) submit();
}

function setChoice(qid: string, key: string) {
  answers.value[qid] = { ...(answers.value[qid] || {}), choice_key: key };
}

async function submit() {
  if (submitting.value || phase.value !== 'paper') return;
  submitting.value = true;
  if (timer) clearInterval(timer);
  const answerList = Object.entries(answers.value).map(([qid, a]) => ({ qid, ...a }));
  // 交卷失败重试 ≤3 次（同幂等键），仍失败 = 作废（口径22）
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const { data } = await examApi.submit(paper.value.paper_id, answerList, 0, idemKey);
      result.value = data;
      phase.value = 'result';
      submitting.value = false;
      return;
    } catch {
      /* retry */
    }
  }
  await examApi.void(paper.value.paper_id).catch(() => undefined);
  submitting.value = false;
  ElMessage.error('网络中断，本卷作废');
  phase.value = 'config';
}

async function abandon() {
  try {
    await ElMessageBox.confirm('放弃将不计成绩，确定作废本卷？', '放弃考核', { type: 'warning' });
  } catch {
    return;
  }
  if (timer) clearInterval(timer);
  await examApi.void(paper.value.paper_id).catch(() => undefined);
  phase.value = 'config';
}

async function loadRecords() {
  const { data } = await examApi.records();
  records.value = data.items;
  recordsTotal.value = data.total;
}

function showRecords() {
  loadRecords();
  phase.value = 'records';
}
</script>

<template>
  <PageShell title="考核模式" subtitle="限时组卷 · 服务端计分 · 中断即作废">
    <template #actions>
      <el-button @click="showRecords">历史成绩</el-button>
    </template>

    <!-- 组卷配置 -->
    <el-card v-if="phase === 'config'" shadow="never" class="q-card">
      <el-form label-width="110px">
        <el-form-item label="词库">
          <el-select v-model="bookId" style="width: 240px" placeholder="选择词库" @change="pickBook">
            <el-option v-for="b in books" :key="b.id" :label="b.name" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="章节">
          <el-select v-model="chapterId" style="width: 240px" placeholder="选择章节">
            <el-option v-for="c in chapters" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="题型 × 题量">
          <div class="tc-row">
            <span class="tc-item"><el-input-number v-model="cfg.dictation" :min="0" :max="30" /> 默写</span>
            <span class="tc-item"><el-input-number v-model="cfg.choice" :min="0" :max="30" /> 选择</span>
            <span class="tc-item"><el-input-number v-model="cfg.cloze" :min="0" :max="30" /> 挖空</span>
            <span class="tc-item"><el-input-number v-model="cfg.listening" :min="0" :max="30" /> 听音</span>
          </div>
        </el-form-item>
        <el-form-item label="限时（分钟）">
          <el-input-number v-model="timeLimit" :min="5" :max="120" />
        </el-form-item>
        <el-form-item label="及格线">
          <el-input-number v-model="passScore" :min="0" :max="100" />
        </el-form-item>
        <el-form-item label="宽松判定">
          <el-switch v-model="loose" />
          <span class="hint">（宽松下近似错误计 0.5 分）</span>
        </el-form-item>
        <el-button type="primary" size="large" @click="start">开始考核（共 {{ totalQuestions }} 题）</el-button>
      </el-form>
    </el-card>

    <!-- 答题 -->
    <el-card v-if="phase === 'paper'" shadow="never" class="q-card">
      <div class="paper-head">
        <el-tag type="danger" size="large">⏱ {{ mmss }}</el-tag>
        <el-tag>失焦 {{ blurCount }} 次</el-tag>
        <el-tag type="info">已答 {{ Object.keys(answers).length }} / {{ paper.questions.length }}</el-tag>
        <el-button type="warning" text @click="abandon">放弃本卷</el-button>
      </div>

      <div v-for="(q, i) in paper.questions" :key="q.qid" class="q-item">
        <p class="q-title">{{ i + 1 }}. {{ q.type === 'dictation' ? '默写：' : q.type === 'choice' ? '选择：' : q.type === 'listening' ? '听音拼写：' : '例句挖空：' }}
          <template v-if="q.type === 'dictation'">{{ q.payload.meaning }}</template>
          <template v-else-if="q.type === 'cloze'">{{ q.payload.sentence_masked }}</template>
          <template v-else-if="q.type === 'listening'">
            播放发音后拼写
            <el-button v-if="q.payload.spelling" circle size="small" style="margin-left: 8px" @click="speakWord(q.payload.spelling)" title="播放发音">
              <el-icon><VideoPlay /></el-icon>
            </el-button>
          </template>
          <template v-else>{{ q.payload.phonetic }}</template>
        </p>
        <div v-if="q.type === 'choice'" class="options">
          <el-button
            v-for="o in q.payload.options"
            :key="o.key"
            :type="answers[q.qid]?.choice_key === o.key ? 'primary' : 'default'"
            @click="setChoice(q.qid, o.key)"
          >
            {{ o.key }}. {{ o.text }}
          </el-button>
        </div>
        <el-input
          v-else
          :model-value="answers[q.qid]?.typed ?? ''"
          placeholder="输入答案（不可回看修改已答题目，禁用粘贴）"
          @paste.prevent
          @update:model-value="(v: string) => (answers[q.qid] = { ...(answers[q.qid] || {}), typed: v })"
        />
      </div>
      <div class="foot-row">
        <el-button type="primary" size="large" :loading="submitting" @click="submit">交卷</el-button>
      </div>
    </el-card>

    <!-- 成绩单 -->
    <el-card v-if="phase === 'result' && result" shadow="never" class="q-card">
      <el-result
        :icon="result.passed ? 'success' : 'warning'"
        :title="`${result.score} 分（${result.passed ? '及格' : '不及格'}）`"
        :sub-title="`正确率 ${(result.correct_rate * 100).toFixed(0)}% · 用时 ${result.duration}s · 失焦 ${result.blur_count} 次`"
      />
      <el-table :data="result.per_question" size="small">
        <el-table-column prop="qid" label="题号" width="80">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column prop="type" label="题型" />
        <el-table-column prop="result" label="判定">
          <template #default="{ row }">
            <el-tag :type="row.result === 'correct' ? 'success' : row.result === 'near' ? 'warning' : 'danger'">
              {{ row.result }}（{{ row.raw_score }} 分）
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div class="foot-row">
        <el-button type="primary" @click="phase = 'config'">再来一卷</el-button>
        <el-button @click="showRecords">历史成绩</el-button>
      </div>
    </el-card>

    <!-- 历史成绩 -->
    <el-card v-if="phase === 'records'" shadow="never" class="q-card">
      <el-table :data="records" size="small">
        <el-table-column prop="created_at" label="时间" width="170" />
        <el-table-column prop="score" label="分数" width="80" />
        <el-table-column prop="duration" label="用时(s)" width="80" />
        <el-table-column prop="blur_count" label="失焦" width="70" />
        <el-table-column label="配置">
          <template #default="{ row }">
            {{ JSON.stringify(row.config_snapshot.type_counts) }} / {{ row.config_snapshot.time_limit_min }}min
          </template>
        </el-table-column>
      </el-table>
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
.tc-row {
  display: flex;
  align-items: center;
  gap: 10px 18px;
  flex-wrap: wrap;
}
.tc-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
.tc-row .el-input-number {
  width: 110px;
}
.hint {
  font-size: 12px;
  color: var(--wt-text-4);
  margin-left: 8px;
}
.paper-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.q-item {
  margin-bottom: 18px;
}
.q-title {
  font-size: 15px;
  color: var(--wt-text);
  margin: 0 0 8px;
}
.options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.foot-row {
  display: flex;
  justify-content: center;
  margin-top: 16px;
  gap: 12px;
}
</style>
