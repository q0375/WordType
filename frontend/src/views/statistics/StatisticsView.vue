<script setup lang="ts">
import { ref, onMounted } from 'vue';
import PageShell from '../../components/PageShell.vue';
import { statsApi } from '../../api';

const dash = ref<any>(null);
const metric = ref<'wpm' | 'accuracy'>('wpm');
const source = ref('all');
const trend = ref<any[]>([]);
const loading = ref(true);

const sources = [
  { value: 'all', label: '全部' },
  { value: 'study', label: '学习' },
  { value: 'practice', label: '练习' },
  { value: 'review', label: '复习' },
  { value: 'exam', label: '考核' },
];

onMounted(async () => {
  dash.value = (await statsApi.dashboard()).data;
  await loadTrend();
  loading.value = false;
});

async function loadTrend() {
  trend.value = (await statsApi.trend(metric.value, source.value, 30)).data.items;
}

function trendPath(): string {
  if (trend.value.length < 2) return '';
  const vals = trend.value.map((t) => t.value);
  const min = Math.min(...vals) * 0.95;
  const max = Math.max(...vals) * 1.05 || 1;
  const W = 640;
  const H = 160;
  return trend.value
    .map((t, i) => {
      const x = (i / (trend.value.length - 1)) * W;
      const y = H - ((t.value - min) / (max - min || 1)) * H;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}
</script>

<template>
  <PageShell title="数据统计" subtitle="趋势 · 熟练度 · 打卡">
    <div v-loading="loading">
      <el-card shadow="never" class="trend-card">
        <template #header>
          <div class="head">
            <span>WPM / 准确率趋势（30 天）</span>
            <div class="filters">
              <el-radio-group v-model="metric" size="small" @change="loadTrend">
                <el-radio-button value="wpm">WPM</el-radio-button>
                <el-radio-button value="accuracy">准确率</el-radio-button>
              </el-radio-group>
              <el-select v-model="source" size="small" style="width: 100px" @change="loadTrend">
                <el-option v-for="s in sources" :key="s.value" :label="s.label" :value="s.value" />
              </el-select>
            </div>
          </div>
        </template>
        <svg viewBox="0 0 640 170" class="trend-svg">
          <path :d="trendPath()" fill="none" stroke="#2563eb" stroke-width="2" />
        </svg>
        <p v-if="trend.length < 2" class="empty-hint">数据点不足，多练习几天后可看趋势</p>
      </el-card>

      <div class="two-col" v-if="dash">
        <el-card shadow="never">
          <template #header><span>熟练度分布</span></template>
          <div class="dist-row">
            <div class="dist-item"><span class="dot mastered"></span>已掌握 <b>{{ dash.proficiency_dist.mastered }}</b></div>
            <div class="dist-item"><span class="dot consolidating"></span>巩固中 <b>{{ dash.proficiency_dist.consolidating }}</b></div>
            <div class="dist-item"><span class="dot danger"></span>高危遗忘 <b>{{ dash.proficiency_dist.danger }}</b></div>
          </div>
        </el-card>
        <el-card shadow="never">
          <template #header><span>未来 7 天复习量</span></template>
          <div class="forecast">
            <div v-for="f in dash.forecast_7d" :key="f.date" class="forecast-item">
              <div class="bar" :style="{ height: 8 + Math.min(70, f.count * 4) + 'px' }"></div>
              <div class="d">{{ f.date.slice(5) }}</div>
            </div>
          </div>
        </el-card>
      </div>
    </div>
  </PageShell>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.filters {
  display: flex;
  gap: 8px;
}
.trend-svg {
  width: 100%;
  height: 180px;
}
.empty-hint {
  color: #9ca3af;
  font-size: 13px;
  text-align: center;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 16px;
}
@media (max-width: 768px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}
.dist-row {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 14px;
  color: #374151;
}
.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  margin-right: 8px;
}
.dot.mastered {
  background: #10b981;
}
.dot.consolidating {
  background: #f59e0b;
}
.dot.danger {
  background: #ef4444;
}
.forecast {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  height: 110px;
}
.forecast-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.forecast-item .bar {
  width: 24px;
  background: #2563eb;
  border-radius: 4px 4px 0 0;
}
.forecast-item .d {
  font-size: 11px;
  color: #9ca3af;
}
</style>
