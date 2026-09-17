<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue';
import PageShell from '../../components/PageShell.vue';
import { useRouter } from 'vue-router';
import { statsApi, adviceApi } from '../../api';

const router = useRouter();
const dash = ref<any>(null);
const advice = ref<any>(null);
const loading = ref(true);

onMounted(async () => {
  try {
    // 统计与建议解耦：统计先返回先渲染，建议卡骨架屏占位，避免白屏
    const { data: d } = await statsApi.dashboard();
    dash.value = d;
  } finally {
    loading.value = false;
  }
  await loadAdvice(true);
});

const isPending = (a: any) => !!a?.note?.includes('生成中');

let pollTimer: number | null = null;
let pollTries = 0;

async function loadAdvice(schedule: boolean) {
  const { data } = await adviceApi.get();
  advice.value = data;
  if (schedule && isPending(data)) startPoll();
}

function startPoll() {
  if (pollTimer) return; // 已在轮询
  const tick = async () => {
    pollTries += 1;
    try {
      const { data } = await adviceApi.get();
      advice.value = data;
    } catch {
      /* 网络抖动忽略，继续等下一轮 */
    }
    if (advice.value && isPending(advice.value) && pollTries < 40) {
      pollTimer = window.setTimeout(tick, 4000);
    } else {
      pollTimer = null;
      pollTries = 0;
    }
  };
  pollTimer = window.setTimeout(tick, 4000);
}

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer);
});

async function refreshAdvice() {
  const { data } = await adviceApi.refresh();
  advice.value = data;
  if (isPending(data)) startPoll();
}

function actionType(type: string) {
  return type === 'high_error' ? 'danger' : type === 'danger_due' ? 'warning' : type === 'inactive' ? 'info' : 'success';
}

function actionRoute(item: any): string {
  const t = item?.action?.type as string | undefined;
  if (t === 'books') return '/books';
  if (t === 'review') return '/review';
  if (t === 'study_plan') return '/study';
  if (t === 'high_error' || t === 'typing_drill') return '/practice';
  return '/practice';
}

const maxForecast = ref(1);
</script>

<template>
  <PageShell title="仪表盘" subtitle="学习概览与 AI 建议">
    <template #actions>
      <el-button type="primary" @click="router.push('/study')">开始学习</el-button>
    </template>

    <div v-if="dash" v-loading="loading">
      <!-- AI 建议 -->
      <el-card class="advice-card" shadow="never">
        <template #header>
          <div class="card-head">
            <span>💡 今日建议（{{ advice?.engine === 'llm' ? 'AI 模型' : advice ? '规则引擎' : '加载中…' }}）</span>
            <div class="head-right">
              <el-tag v-if="advice && isPending(advice)" type="primary" size="small">AI 生成中，稍候自动更新…</el-tag>
              <el-tooltip v-else-if="advice?.note" :content="advice.note" placement="top">
                <el-tag type="warning" size="small">AI 调用失败，已回退 ⓘ</el-tag>
              </el-tooltip>
              <el-button v-if="advice" text size="small" @click="refreshAdvice">换一批</el-button>
            </div>
          </div>
        </template>
        <div v-if="advice?.items?.length" class="advice-list">
          <div v-for="item in advice.items" :key="item.type" class="advice-item">
            <el-tag :type="actionType(item.type)" size="small">{{ item.message }}</el-tag>
            <el-button size="small" text type="primary" @click="router.push(actionRoute(item))">去执行</el-button>
          </div>
        </div>
        <el-skeleton v-else :rows="3" animated />
      </el-card>

      <!-- 统计卡片 -->
      <div class="stat-grid">
        <el-card shadow="never">
          <div class="stat-num">{{ dash.today.new_learned }}</div>
          <div class="stat-label">今日新学</div>
        </el-card>
        <el-card shadow="never">
          <div class="stat-num">{{ dash.today.review_due }}</div>
          <div class="stat-label">今日待复习</div>
        </el-card>
        <el-card shadow="never">
          <div class="stat-num">{{ dash.today.streak_days }}</div>
          <div class="stat-label">连续打卡（天）</div>
        </el-card>
        <el-card shadow="never">
          <div class="stat-num">{{ dash.week_wpm }}</div>
          <div class="stat-label">本周 WPM</div>
        </el-card>
      </div>

      <div class="two-col">
        <!-- 打卡热力图 -->
        <el-card shadow="never">
          <template #header><span>打卡热力图（近 12 周）</span></template>
          <div class="heatmap">
            <div
              v-for="d in dash.heatmap"
              :key="d.date"
              class="heat-cell"
              :title="`${d.date}：新学 ${d.new_count} / 复习 ${d.review_count}`"
              :style="{ opacity: 0.25 + Math.min(0.75, (d.new_count + d.review_count) / 40) }"
            ></div>
          </div>
        </el-card>

        <!-- 熟练度分布 -->
        <el-card shadow="never">
          <template #header><span>熟练度分布</span></template>
          <div class="dist-row">
            <div class="dist-item">
              <span class="dot mastered"></span>已掌握 <b>{{ dash.proficiency_dist.mastered }}</b>
            </div>
            <div class="dist-item">
              <span class="dot consolidating"></span>巩固中 <b>{{ dash.proficiency_dist.consolidating }}</b>
            </div>
            <div class="dist-item">
              <span class="dot danger"></span>高危遗忘 <b>{{ dash.proficiency_dist.danger }}</b>
            </div>
          </div>
        </el-card>
      </div>

      <!-- 未来 7 天复习量 -->
      <el-card shadow="never" class="forecast-card">
        <template #header><span>未来 7 天复习量</span></template>
        <div class="forecast">
          <div v-for="f in dash.forecast_7d" :key="f.date" class="forecast-item">
            <div class="bar" :style="{ height: 8 + Math.min(80, f.count * 4) + 'px' }"></div>
            <div class="cnt">{{ f.count }}</div>
            <div class="d">{{ f.date.slice(5) }}</div>
          </div>
        </div>
      </el-card>
    </div>
    <el-empty v-else-if="!loading" description="暂无数据，先去学习几个单词吧" />
  </PageShell>
</template>

<style scoped>
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.advice-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.advice-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}
.stat-num {
  font-size: 32px;
  font-weight: 700;
  color: var(--wt-primary);
}
.stat-label {
  font-size: 13px;
  color: var(--wt-text-3);
  margin-top: 4px;
}
.two-col {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
}
@media (max-width: 768px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}
.heatmap {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
}
.heat-cell {
  width: 12px;
  height: 12px;
  border-radius: 2px;
  background: var(--wt-success);
}
.dist-row {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 14px;
  color: var(--wt-text-2);
}
.dist-item b {
  margin-left: 4px;
}
.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 8px;
}
.dot.mastered {
  background: var(--wt-success);
}
.dot.consolidating {
  background: var(--wt-warning);
}
.dot.danger {
  background: var(--wt-danger);
}
.forecast {
  display: flex;
  align-items: flex-end;
  gap: 24px;
  height: 130px;
}
.forecast-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.forecast-item .bar {
  width: 28px;
  background: var(--wt-primary);
  border-radius: 4px 4px 0 0;
}
.forecast-item .cnt {
  font-size: 12px;
  color: var(--wt-text);
  font-weight: 600;
}
.forecast-item .d {
  font-size: 11px;
  color: var(--wt-text-4);
}
</style>
