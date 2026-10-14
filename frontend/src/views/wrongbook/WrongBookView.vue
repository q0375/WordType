<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { wrongbookApi } from '../../api';
import { confirmAction } from '../../core/confirm';

const items = ref<any[]>([]);
const total = ref(0);
const resolved = ref(0);
const pinnedOnly = ref(false);
const loading = ref(false);
const page = ref(1);
const PAGE_SIZE = 50;
const selected = ref<any[]>([]);

onMounted(load);

async function load() {
  loading.value = true;
  try {
    const { data } = await wrongbookApi.list({ resolved: resolved.value, pinned: pinnedOnly.value ? 1 : undefined, page: page.value, page_size: PAGE_SIZE });
    items.value = data.items;
    total.value = data.total;
  } finally {
    loading.value = false;
  }
}

function onPageChange(p: number) {
  page.value = p;
  load();
}

function onSelectionChange(rows: any[]) {
  selected.value = rows;
}

async function togglePin(item: any) {
  await wrongbookApi.patch(item.word_id, { pinned: item.pinned ? 0 : 1 });
  load();
}

/** 手动移出：绕过「连对 3 次自动攻克」规则，需确认（2026-10-04 补入危险操作确认清单） */
async function resolve(item: any) {
  const left = 3 - (item.conquer_count || 0);
  const ok = await confirmAction({
    title: '移出错题本',
    message: left > 0
      ? `确定把「${item.spelling}」移出错题本？当前攻克进度 ${item.conquer_count || 0}/3，移出后本词不再出现在「在册」列表，需在「已攻克」里手动翻找。`
      : `确定把「${item.spelling}」移出错题本？`,
    confirmText: '移出',
    cancelText: '取消',
  });
  if (!ok) return;
  await wrongbookApi.patch(item.word_id, { resolved: 1 });
  ElMessage.success({ message: '已移出错题本', duration: 2000, showClose: true });
  load();
}

/** 批量移出（2026-10-04）：同样绕过连对 3 规则，批量误触代价更高，确认文案列明数量 */
async function batchResolve() {
  const rows = selected.value.filter((r) => !r.resolved);
  if (!rows.length) return;
  const names = rows.slice(0, 5).map((r) => r.spelling).join('、');
  const more = rows.length > 5 ? ` 等 ${rows.length} 个词` : '';
  const ok = await confirmAction({
    title: '批量移出错题本',
    message: `确定把选中的 ${rows.length} 个词（${names}${more}）全部移出错题本？移出会绕过「连对 3 次自动攻克」规则，且不再出现在「在册」列表，需在「已攻克」里手动翻找。`,
    confirmText: `移出 ${rows.length} 个`,
    cancelText: '取消',
  });
  if (!ok) return;
  try {
    const { data } = await wrongbookApi.batchPatch(rows.map((r) => r.word_id), { resolved: 1 });
    ElMessage.success({ message: `已移出 ${data?.updated ?? rows.length} 个词`, duration: 2000, showClose: true });
  } finally {
    selected.value = [];
    load();
  }
}

const sourceTag: Record<string, string> = {
  study: '学习', practice: '练习', review: '复习', exam: '考核', game: '游戏',
};
</script>

<template>
  <PageShell title="错题本" subtitle="连对 3 次自动移出 · 可置顶/手动移入移出">
    <div class="toolbar">
      <el-radio-group v-model="resolved" @change="page = 1; selected = []; load()">
        <el-radio-button :value="0">在册</el-radio-button>
        <el-radio-button :value="1">已攻克</el-radio-button>
      </el-radio-group>
      <el-checkbox v-model="pinnedOnly" @change="page = 1; load()">只看置顶</el-checkbox>
      <el-button
        v-if="!resolved"
        type="danger"
        plain
        :disabled="!selected.length"
        @click="batchResolve"
      >批量移出{{ selected.length ? `（${selected.length}）` : '' }}</el-button>
    </div>

    <el-table :data="items" v-loading="loading" size="default" @selection-change="onSelectionChange">
      <el-table-column v-if="!resolved" type="selection" width="42" />
      <el-table-column prop="spelling" label="单词" width="160" />
      <el-table-column prop="meaning" label="释义" />
      <el-table-column label="来源" width="90">
        <template #default="{ row }"><el-tag size="small">{{ sourceTag[row.source] || row.source }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="conquer_count" label="攻克计数" width="100">
        <template #default="{ row }">
          <el-tag :type="row.conquer_count >= 2 ? 'success' : 'info'" size="small">{{ row.conquer_count }}/3</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="added_at" label="加入时间" width="180" />
      <el-table-column label="操作" width="180">
        <template #default="{ row }">
          <el-button size="small" @click="togglePin(row)">{{ row.pinned ? '取消置顶' : '置顶' }}</el-button>
          <el-button v-if="!resolved" size="small" type="success" plain @click="resolve(row)">移出</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div style="margin-top: 12px; display: flex; justify-content: flex-end">
      <el-pagination
        v-model:current-page="page"
        :page-size="PAGE_SIZE"
        :total="total"
        layout="total, prev, pager, next"
        background
        hide-on-single-page
        @current-change="onPageChange"
      />
    </div>
    <el-empty v-if="!items.length && !loading" description="错题本是空的，保持下去！" />
  </PageShell>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}
</style>
