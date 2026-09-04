<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
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
const ai = ref<any>(null);
const aiForm = ref<any>({ engine: 'rule', api_base_url: '', api_key: '', model_name: '', temperature: 0.7, timeout_s: 30, enabled: false });
const testResult = ref<any>(null);

onMounted(() => {
  loadUsers();
  loadCodes();
  loadAI();
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

async function loadAI() {
  const { data } = await adminApi.aiConfig();
  ai.value = data;
  aiForm.value = { ...data, api_key: '' };
}

async function saveAI() {
  const patch: any = { ...aiForm.value };
  if (!patch.api_key) delete patch.api_key;
  ai.value = await adminApi.putAiConfig(patch).then((r) => r.data);
  ElMessage.success('AI 配置已保存（key 加密存储，不回显明文）');
}

async function testAI() {
  testResult.value = await adminApi.testAiConfig().then((r) => r.data);
}
</script>

<template>
  <PageShell title="管理后台" subtitle="用户 · 邀请码 · AI 配置（admin）">
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

      <!-- AI 配置 -->
      <el-tab-pane label="AI 配置" name="ai">
        <el-card shadow="never" class="ai-card" v-if="aiForm">
          <el-form label-width="140px">
            <el-form-item label="建议引擎">
              <el-radio-group v-model="aiForm.engine">
                <el-radio value="rule">规则引擎（免费）</el-radio>
                <el-radio value="llm">LLM（OpenAI 兼容）</el-radio>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="启用 LLM">
              <el-switch v-model="aiForm.enabled" />
            </el-form-item>
            <el-form-item label="API 基地址">
              <el-input v-model="aiForm.api_base_url" placeholder="https://api.example.com/v1" />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input v-model="aiForm.api_key" type="password" show-password :placeholder="ai?.api_key_masked ? `已配置（${ai.api_key_masked}），留空则不修改` : '输入新 Key'" />
            </el-form-item>
            <el-form-item label="模型名">
              <el-input v-model="aiForm.model_name" placeholder="gpt-4o-mini 等" />
            </el-form-item>
            <el-form-item label="温度">
              <el-input-number v-model="aiForm.temperature" :min="0" :max="2" :step="0.1" />
            </el-form-item>
            <el-form-item label="超时（秒）">
              <el-input-number v-model="aiForm.timeout_s" :min="1" :max="300" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="saveAI">保存</el-button>
              <el-button @click="testAI">连通性测试</el-button>
            </el-form-item>
          </el-form>
          <el-alert
            v-if="testResult"
            :type="testResult.ok ? 'success' : 'error'"
            :closable="false"
            :title="`延迟 ${testResult.latency_ms}ms · ${testResult.message}`"
          />
          <p class="hint">调用失败将静默回落规则引擎；手动刷新限 3 次/人/日；key AES-GCM 加密存储，永不回显明文。</p>
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
  color: #9ca3af;
}
</style>
