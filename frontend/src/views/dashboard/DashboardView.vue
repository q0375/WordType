<script setup lang="ts">
import { ref, onMounted } from 'vue';
import PageShell from '../../components/PageShell.vue';
import { useRouter } from 'vue-router';
import { statsApi, adviceApi } from '../../api';

const router = useRouter();
const dash = ref<any>(null);
const advice = ref<any>(null);
const loading = ref(true);

onMounted(async () => {
  try {
    const [d, a] = await Promise.all([statsApi.dashboard(), adviceApi.get()]);
    dash.value = d.data;
    advice.value = a.data;
  } finally {
    loading.value = false;
  }
});

async function refreshAdvice() {
  const { data } = await adviceApi.refresh();
  advice.value = data;
}

function actionType(type: string) {
  return type === 'high_error' ? 'danger' : type === 'danger_due' ? 'warning' : type === 'inactive' ? 'info' : 'success';
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
      <el-card class="advice-card" shadow="never" v-if="advice?.items?.length">
        <template #header>
          <div class="card-head">
            <span>💡 今日建议（{{ advice.engine === 'rule' ? '规则引擎' : 'AI 模型' }}）</span>
            <el-button text size="small" @click="refreshAdvice">换一批</el-button>
          </div>
        </template>
        <div class="advice-list">
          <div v-for="item in advice.items" :key="item.type" class="advice-item">
            <el-tag :type="actionType(item.type)" size="small">{{ item.message }}</el-tag>
            <el-button size="small" text type="primary" @click="router.push('/practice')">去执行</el-button>
          </div>
        </div>
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
  color: #2563eb;
}
.stat-label {
  font-size: 13px;
  color: #6b7280;
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
  background: #10b981;
}
.dist-row {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 14px;
  color: #374151;
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
  background: #2563eb;
  border-radius: 4px 4px 0 0;
}
.forecast-item .cnt {
  font-size: 12px;
  color: #111827;
  font-weight: 600;
}
.forecast-item .d {
  font-size: 11px;
  color: #9ca3af;
}
</style>
