<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { wrongbookApi } from '../../api';

const items = ref<any[]>([]);
const total = ref(0);
const resolved = ref(0);
const pinnedOnly = ref(false);
const loading = ref(false);

onMounted(load);

async function load() {
  loading.value = true;
  try {
    const { data } = await wrongbookApi.list({ resolved: resolved.value, pinned: pinnedOnly.value ? 1 : undefined });
    items.value = data.items;
    total.value = data.total;
  } finally {
    loading.value = false;
  }
}

async function togglePin(item: any) {
  await wrongbookApi.patch(item.word_id, { pinned: item.pinned ? 0 : 1 });
  load();
}

async function resolve(item: any) {
  await wrongbookApi.patch(item.word_id, { resolved: 1 });
  ElMessage.success('已移出错题本');
  load();
}

const sourceTag: Record<string, string> = {
  study: '学习', practice: '练习', review: '复习', exam: '考核', game: '游戏',
};
</script>

<template>
  <PageShell title="错题本" subtitle="连对 3 次自动移出 · 可置顶/手动移入移出">
    <div class="toolbar">
      <el-radio-group v-model="resolved" @change="load">
        <el-radio-button :value="0">在册</el-radio-button>
        <el-radio-button :value="1">已攻克</el-radio-button>
      </el-radio-group>
      <el-checkbox v-model="pinnedOnly" @change="load">只看置顶</el-checkbox>
    </div>

    <el-table :data="items" v-loading="loading" size="default">
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
