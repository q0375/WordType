<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { authApi, exportApi } from '../../api';
import { useAuthStore } from '../../stores/auth';
import http from '../../api/http';

const router = useRouter();
const auth = useAuthStore();
const pwd = ref({ old_password: '', new_password: '', confirm: '' });
const stats = ref<any>(null);

async function changePassword() {
  if (pwd.value.new_password !== pwd.value.confirm) {
    ElMessage.warning('两次输入的新密码不一致');
    return;
  }
  if (pwd.value.new_password.length < 8 || !/[a-zA-Z]/.test(pwd.value.new_password) || !/\d/.test(pwd.value.new_password)) {
    ElMessage.warning('密码需至少 8 位且同时包含字母与数字');
    return;
  }
  // 改密响应签发新 token（口径16）：当前会话无感续用，其他设备全部失效
  const { data } = await authApi.changePassword(pwd.value.old_password, pwd.value.new_password);
  auth.setSession(data.token, data.user);
  pwd.value = { old_password: '', new_password: '', confirm: '' };
  ElMessage.success('密码已修改，其他设备已强制下线');
}

async function deactivate() {
  await ElMessageBox.confirm('注销后 30 天内可联系管理员恢复，到期物理删除全部数据。确定？', '账号注销', { type: 'error' });
  await authApi.deactivate();
  auth.logout();
  router.push('/login');
}

onMounted(async () => {
  const { data } = await http.get('/stats/dashboard');
  stats.value = data;
});
</script>

<template>
  <PageShell title="个人中心" subtitle="改密 · 数据导出 · 注销">
    <div class="grid">
      <el-card shadow="never">
        <template #header><span>账号信息</span></template>
        <p>用户名：<b>{{ auth.user?.username }}</b></p>
        <p>角色：<el-tag size="small" :type="auth.isAdmin ? 'warning' : 'info'">{{ auth.user?.role }}</el-tag></p>
        <p v-if="stats" class="muted">
          累计新学 {{ stats.today.new_learned }}（今日） · 连续打卡 {{ stats.today.streak_days }} 天 · 本周 WPM {{ stats.week_wpm }}
        </p>
      </el-card>

      <el-card shadow="never">
        <template #header><span>修改密码</span></template>
        <el-form label-width="90px">
          <el-form-item label="旧密码">
            <el-input v-model="pwd.old_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input v-model="pwd.new_password" type="password" show-password placeholder="≥8 位，含字母与数字" />
          </el-form-item>
          <el-form-item label="确认新密码">
            <el-input v-model="pwd.confirm" type="password" show-password />
          </el-form-item>
          <el-button type="primary" @click="changePassword">修改</el-button>
        </el-form>
      </el-card>

      <el-card shadow="never">
        <template #header><span>数据与注销</span></template>
        <p class="muted">导出个人全量数据（ZIP：学习统计 / 打字记录 / 打卡 / 考核 / 游戏 / 错题本 CSV）。</p>
        <a :href="exportApi.accountUrl" target="_blank">
          <el-button type="primary">导出我的数据</el-button>
        </a>
        <el-divider />
        <p class="muted">注销为软删除，30 天后定时任务物理清理（含私有词库与全部统计）。</p>
        <el-button type="danger" @click="deactivate">注销账号</el-button>
      </el-card>
    </div>
  </PageShell>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}
.muted {
  color: #6b7280;
  font-size: 13px;
}
</style>
