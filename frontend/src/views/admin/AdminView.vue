<script setup lang="ts">
import { ref, watch, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { adminApi } from '../../api';

const tab = ref('users');
const users = ref<any[]>([]);
const usersTotal = ref(0);
const userQuery = ref('');
const userStatus = ref('all');
const codes = ref<any[]>([]);
const codesTotal = ref(0);
const codeStatus = ref('');

onMounted(() => {
  loadUsers();
  loadCodes();
  loadDevSummary();
});

async function loadUsers() {
  const { data } = await adminApi.users({ q: userQuery.value || undefined, status: userStatus.value });
  users.value = data.items;
  usersTotal.value = data.total;
}

async function resetPassword(u: any) {
  const { data } = await adminApi.resetPassword(u.id);
  await ElMessageBox.alert(`临时密码（仅本次可见，请转告用户并提示首次登录改密）：\n${data.temp_password}`, '重置成功');
}

async function restoreUser(u: any) {
  try {
    await adminApi.restore(u.id);
    ElMessage.success('已恢复');
    loadUsers();
  } catch {
    /* RESTORE_WINDOW_EXPIRED 由拦截器提示 */
  }
}

async function loadCodes() {
  const { data } = await adminApi.inviteCodes({ status: codeStatus.value || undefined });
  codes.value = data.items;
  codesTotal.value = data.total;
}

async function genCodes() {
  const { value } = await ElMessageBox.prompt('生成数量（1–100）与有效天数，格式：数量,天数', '批量生成邀请码', { inputValue: '10,30' });
  const [count, days] = value.split(',').map((s: string) => parseInt(s.trim(), 10));
  const { data } = await adminApi.createInviteCodes(count, days);
  await ElMessageBox.alert(data.codes.join('\n'), '已生成（一次性使用）');
  loadCodes();
}

async function toggleCode(c: any) {
  await adminApi.patchInviteCode(c.id, c.is_active ? 0 : 1);
  loadCodes();
}

// ---- 测试数据调整（dev-data） ----
const devUser = ref<number | null>(null); // null = 管理员自己
const devSummary = ref<any>(null);
const devSeeding = ref(false);
const dev = ref({
  high_error_n: 10,
  danger_due_n: 5,
  inactive_days: 0,
  weak_bigram_n: 5,
  typing_count: 30,
  typing_wpm: 40,
  typing_accuracy: 0.9,
  game_count: 10,
});

async function loadDevSummary() {
  const { data } = await adminApi.devdataSummary(devUser.value ?? undefined);
  devSummary.value = data;
}

async function seedDevData() {
  devSeeding.value = true;
  try {
    const p: any = { target_user_id: devUser.value ?? undefined };
    for (const [k, v] of Object.entries(dev.value)) {
      if (v && v > 0) p[k] = v;
    }
    const { data } = await adminApi.devdataSeed(p);
    devSummary.value = data.summary;
    await ElMessageBox.alert(data.done.join('\n') || '未选择任何注入项', '注入完成');
  } finally {
    devSeeding.value = false;
  }
}

async function clearDevData() {
  await ElMessageBox.confirm('将删除该用户的全部学习数据（词统计/错题/打字/游戏/考核/复习队列等），不可恢复。确认？', '清空学习数据', {
    type: 'warning',
    confirmButtonText: '全部清空',
    confirmButtonClass: 'el-button--danger',
  });
  const { data } = await adminApi.devdataClear(devUser.value ?? undefined);
  devSummary.value = data.summary;
  ElMessage.success(`已清除 ${data.cleared_rows} 行数据`);
}

watch(devUser, loadDevSummary);
</script>

<template>
  <PageShell title="管理后台" subtitle="用户 · 邀请码 · 测试数据（admin）">
    <el-tabs v-model="tab">
      <!-- 用户管理 -->
      <el-tab-pane label="用户管理" name="users">
        <div class="toolbar">
          <el-input v-model="userQuery" placeholder="搜索用户名" style="width: 200px" clearable @keyup.enter="loadUsers" />
          <el-radio-group v-model="userStatus" @change="loadUsers">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="active">正常</el-radio-button>
            <el-radio-button value="deleted">已注销</el-radio-button>
          </el-radio-group>
          <el-button @click="loadUsers">查询</el-button>
        </div>
        <el-table :data="users" size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" label="用户名" />
          <el-table-column prop="role" label="角色" width="90" />
          <el-table-column label="状态" width="140">
            <template #default="{ row }">
              <el-tag v-if="!row.is_deleted" type="success" size="small">正常</el-tag>
              <el-tag v-else type="danger" size="small">已注销{{ row.deleted_at ? `（${row.deleted_at.slice(0, 10)}）` : '' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="last_login_at" label="最近登录" width="170" />
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <el-button size="small" type="warning" plain @click="resetPassword(row)">重置密码</el-button>
              <el-button v-if="row.is_deleted" size="small" type="success" plain @click="restoreUser(row)">恢复</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 邀请码 -->
      <el-tab-pane label="邀请码管理" name="codes">
        <div class="toolbar">
          <el-button type="primary" @click="genCodes">批量生成</el-button>
          <el-select v-model="codeStatus" placeholder="状态筛选" clearable style="width: 140px" @change="loadCodes">
            <el-option label="可用" value="active" />
            <el-option label="已使用" value="used" />
            <el-option label="已停用" value="disabled" />
          </el-select>
        </div>
        <el-table :data="codes" size="small">
          <el-table-column prop="code" label="邀请码" width="160" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag v-if="row.used_by" type="info" size="small">已使用</el-tag>
              <el-tag v-else-if="row.is_active" type="success" size="small">可用</el-tag>
              <el-tag v-else type="danger" size="small">已停用</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="used_at" label="使用时间" width="170" />
          <el-table-column prop="expires_at" label="过期时间" width="170" />
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button v-if="!row.used_by" size="small" :type="row.is_active ? 'danger' : 'success'" plain @click="toggleCode(row)">
                {{ row.is_active ? '停用' : '启用' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 测试数据调整 -->
      <el-tab-pane label="测试数据" name="devdata">
        <el-card shadow="never" class="ai-card">
          <el-form label-width="150px">
            <el-form-item label="目标用户">
              <el-select v-model="devUser" placeholder="我自己（admin）" clearable filterable style="width: 300px">
                <el-option v-for="u in users" :key="u.id" :label="`#${u.id} ${u.username}（${u.role}）`" :value="u.id" />
              </el-select>
            </el-form-item>
          </el-form>

          <el-descriptions v-if="devSummary" :column="3" size="small" border style="margin-bottom: 8px">
            <el-descriptions-item label="词统计条数">{{ devSummary.word_stat_total }}</el-descriptions-item>
            <el-descriptions-item label="高错误率词">{{ devSummary.high_error }}</el-descriptions-item>
            <el-descriptions-item label="高危明日到期">{{ devSummary.danger_due_tomorrow }}</el-descriptions-item>
            <el-descriptions-item label="活跃天数">{{ devSummary.activity_days }}</el-descriptions-item>
            <el-descriptions-item label="打字记录">{{ devSummary.typing_records }}</el-descriptions-item>
            <el-descriptions-item label="游戏记录">{{ devSummary.game_records }}</el-descriptions-item>
            <el-descriptions-item label="错题（未攻克）">{{ devSummary.wrong_book }}</el-descriptions-item>
            <el-descriptions-item label="考核记录">{{ devSummary.exam_records }}</el-descriptions-item>
            <el-descriptions-item label="指法统计">{{ devSummary.letter_stats }}</el-descriptions-item>
          </el-descriptions>

          <el-divider content-position="left">注入测试数据（0 = 跳过该项）</el-divider>
          <el-form label-width="150px">
            <el-form-item label="高错误率词数">
              <el-input-number v-model="dev.high_error_n" :min="0" :max="200" />
              <span class="field-hint">错误率 &gt;60%，触发「专项练习」建议并入错题本</span>
            </el-form-item>
            <el-form-item label="高危明日到期词数">
              <el-input-number v-model="dev.danger_due_n" :min="0" :max="200" />
              <span class="field-hint">触发「复习」建议并写入复习队列</span>
            </el-form-item>
            <el-form-item label="清除近 N 天活跃">
              <el-input-number v-model="dev.inactive_days" :min="0" :max="30" />
              <span class="field-hint">≥7 天触发「连续未学」建议</span>
            </el-form-item>
            <el-form-item label="弱指法组合数">
              <el-input-number v-model="dev.weak_bigram_n" :min="0" :max="200" />
              <span class="field-hint">触发「指法专项」建议</span>
            </el-form-item>
            <el-form-item label="打字记录条数">
              <el-input-number v-model="dev.typing_count" :min="0" :max="200" />
              <span class="field-hint">随机分布在近 7 天，驱动趋势图</span>
            </el-form-item>
            <el-form-item label="打字 WPM / 正确率">
              <el-input-number v-model="dev.typing_wpm" :min="5" :max="300" />
              <el-input-number v-model="dev.typing_accuracy" :min="0.1" :max="1" :step="0.01" style="margin-left: 8px" />
            </el-form-item>
            <el-form-item label="游戏记录条数">
              <el-input-number v-model="dev.game_count" :min="0" :max="200" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="devSeeding" @click="seedDevData">注入数据</el-button>
              <el-button @click="loadDevSummary">刷新统计</el-button>
              <el-button type="danger" plain @click="clearDevData">清空该用户全部学习数据</el-button>
            </el-form-item>
          </el-form>
          <p class="hint">注入/清空后当日 AI 建议缓存自动失效，仪表盘与建议立即按新数据重算。仅测试用途。</p>
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </PageShell>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
  align-items: center;
}
.ai-card {
  max-width: 640px;
}
.hint {
  font-size: 12px;
  color: var(--wt-text-4);
}
.field-hint {
  font-size: 12px;
  color: var(--wt-text-4);
  margin-left: 10px;
}
</style>
