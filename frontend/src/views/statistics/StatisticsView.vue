<script setup lang="ts">
import { ref, onMounted } from 'vue';
import PageShell from '../../components/PageShell.vue';
import { statsApi } from '../../api';

const dash = ref<any>(null);
// 2026-09-23：wpm = typing_record 单词级实时聚合（学习/练习/复习/游戏每词写入）；
// accuracy/volume = daily_activity 日聚合。每次进入页面/点刷新即拉最新数据。
const metric = ref<'wpm' | 'accuracy' | 'volume'>('accuracy');
const trend = ref<any[]>([]);
const loading = ref(true);

onMounted(reload);

async function loadTrend() {
  trend.value = (await statsApi.trend(metric.value, 30)).data.items;
}

// 绘图区几何（viewBox 640×200）：左侧 46px 留给 Y 轴刻度，底部 26px 留给日期
const PLOT = { x0: 46, x1: 628, y0: 12, y1: 150 };

/** 纵轴范围：准确率固定夹在 0~1；其余按数据留白，避免曲线贴边 */
function yScale(): { min: number; max: number } {
  const vals = trend.value.map((t) => Number(t.value) || 0);
  if (!vals.length) return { min: 0, max: 1 };
  let min = Math.min(...vals);
  let max = Math.max(...vals);
  if (metric.value === 'accuracy') {
    min = Math.max(0, min - 0.05);
    max = Math.min(1, max + 0.05);
  } else {
    const pad = (max - min) * 0.2 + (max > 0 ? max * 0.1 : 1);
    min = Math.max(0, min - pad);
    max = max + pad;
  }
  if (max - min < 1e-6) {
    min -= 1;
    max += 1;
  }
  return { min, max };
}

function pxAt(i: number): number {
  return PLOT.x0 + (i / Math.max(trend.value.length - 1, 1)) * (PLOT.x1 - PLOT.x0);
}
function pyAt(v: number, s: { min: number; max: number }): number {
  return PLOT.y1 - ((v - s.min) / (s.max - s.min)) * (PLOT.y1 - PLOT.y0);
}
function fmtVal(v: number): string {
  if (metric.value === 'accuracy') return `${Math.round(v * 100)}%`;
  return String(Math.round(v * 10) / 10);
}
function trendPath(): string {
  if (trend.value.length < 2) return '';
  const s = yScale();
  return trend.value
    .map((t, i) => `${i === 0 ? 'M' : 'L'}${pxAt(i).toFixed(1)},${pyAt(Number(t.value) || 0, s).toFixed(1)}`)
    .join(' ');
}
/** 折线上的数据点（≤12 点时才画圆点，避免密集重叠） */
function points(): { x: number; y: number }[] {
  const s = yScale();
  if (trend.value.length > 12) return [];
  return trend.value.map((t, i) => ({ x: pxAt(i), y: pyAt(Number(t.value) || 0, s) }));
}
/** Y 轴刻度：低 / 中 / 高三条 */
function yTicks(): { y: number; label: string }[] {
  const s = yScale();
  return [s.min, (s.min + s.max) / 2, s.max].map((v) => ({ y: pyAt(v, s), label: fmtVal(v) }));
}
/** X 轴刻度：首 / 中 / 末日期（数据点多时按 5 等分取样） */
function xTicks(): { x: number; label: string }[] {
  const n = trend.value.length;
  if (!n) return [];
  const idx = n <= 4 ? [...Array(n).keys()] : [0, Math.floor((n - 1) / 4), Math.floor((n - 1) / 2), Math.floor(((n - 1) * 3) / 4), n - 1];
  return [...new Set(idx)].map((i) => ({ x: pxAt(i), label: trend.value[i].date.slice(5) }));
}

function trendText(): string {
  if (!trend.value.length) return '';
  const last = trend.value[trend.value.length - 1];
  if (metric.value === 'wpm') return `${last.date.slice(5)} 打字速度 ${last.value} WPM`;
  return metric.value === 'accuracy'
    ? `${last.date.slice(5)} 正确率 ${Math.round(last.value * 100)}%`
    : `${last.date.slice(5)} 完成 ${last.value} 词`;
}

async function reload() {
  loading.value = true;
  try {
    dash.value = (await statsApi.dashboard()).data;
    await loadTrend();
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <PageShell title="数据统计" subtitle="趋势 · 熟练度 · 打卡">
    <div v-loading="loading">
      <el-card shadow="never" class="trend-card">
        <template #header>
          <div class="head">
            <span>每日趋势（30 天）</span>
            <div class="filters">
              <el-radio-group v-model="metric" size="small" @change="loadTrend">
                <el-radio-button value="wpm">WPM</el-radio-button>
                <el-radio-button value="accuracy">正确率</el-radio-button>
                <el-radio-button value="volume">完成量</el-radio-button>
              </el-radio-group>
              <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
            </div>
          </div>
        </template>
        <svg viewBox="0 0 640 186" class="trend-svg">
          <!-- 网格与 Y 轴刻度 -->
          <g v-for="t in yTicks()" :key="'y' + t.label">
            <line :x1="PLOT.x0" :x2="PLOT.x1" :y1="t.y" :y2="t.y" class="trend-grid" />
            <text :x="PLOT.x0 - 6" :y="t.y + 4" class="trend-axis-label" text-anchor="end">{{ t.label }}</text>
          </g>
          <!-- 坐标轴 -->
          <line :x1="PLOT.x0" :x2="PLOT.x1" :y1="PLOT.y1" :y2="PLOT.y1" class="trend-axis" />
          <line :x1="PLOT.x0" :x2="PLOT.x0" :y1="PLOT.y0 - 4" :y2="PLOT.y1" class="trend-axis" />
          <!-- 曲线与数据点 -->
          <path :d="trendPath()" fill="none" class="trend-line" stroke-width="2" />
          <circle v-for="(p, i) in points()" :key="'p' + i" :cx="p.x" :cy="p.y" r="3" class="trend-dot" />
          <!-- X 轴日期 -->
          <text
            v-for="(t, i) in xTicks()"
            :key="'x' + i"
            :x="t.x"
            :y="PLOT.y1 + 16"
            class="trend-axis-label"
            :text-anchor="i === 0 ? 'start' : i === xTicks().length - 1 ? 'end' : 'middle'"
          >{{ t.label }}</text>
        </svg>
        <p v-if="trend.length" class="empty-hint">{{ trendText() }}</p>
        <p v-if="trend.length < 2" class="empty-hint">
          {{ metric === 'wpm' ? '暂无打字速度数据：WPM = 每分钟打字词数（5 字符折合 1 词），学习/练习/复习/游戏每答一词即实时计入' : '数据点不足，多练习几天后可看趋势（学习/练习/复习/考核/游戏均计入）' }}
        </p>
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
  height: 196px;
}
.trend-line {
  stroke: var(--wt-primary);
}
.trend-grid {
  stroke: var(--wt-border);
  stroke-dasharray: 3 4;
  stroke-width: 1;
}
.trend-axis {
  stroke: var(--wt-text-4);
  stroke-width: 1;
}
.trend-axis-label {
  fill: var(--wt-text-4);
  font-size: 11px;
}
.trend-dot {
  fill: var(--wt-primary);
}
.empty-hint {
  color: var(--wt-text-4);
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
  color: var(--wt-text-2);
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
  background: var(--wt-primary);
  border-radius: 4px 4px 0 0;
}
.forecast-item .d {
  font-size: 11px;
  color: var(--wt-text-4);
}
</style>
