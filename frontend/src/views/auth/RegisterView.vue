<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import { authApi } from '../../api';
import { useAuthStore } from '../../stores/auth';
import { ApiError } from '../../api/http';

const router = useRouter();
const auth = useAuthStore();

const form = ref({ username: '', password: '', confirm: '', invite_code: '' });
const loading = ref(false);

async function submit() {
  if (!/^[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}$/.test(form.value.username)) {
    ElMessage.warning({ message: '用户名需为 3-20 位字母/数字/下划线/中文', duration: 2000, showClose: true });
    return;
  }
  if (form.value.password.length < 8 || !/[a-zA-Z]/.test(form.value.password) || !/\d/.test(form.value.password)) {
    ElMessage.warning({ message: '密码需至少 8 位且同时包含字母与数字', duration: 2000, showClose: true });
    return;
  }
  if (form.value.password !== form.value.confirm) {
    ElMessage.warning({ message: '两次输入的密码不一致', duration: 2000, showClose: true });
    return;
  }
  if (!form.value.invite_code.trim()) {
    ElMessage.warning({ message: '请填写邀请码（请联系管理员获取）', duration: 2000, showClose: true });
    return;
  }
  loading.value = true;
  try {
    const { data } = await authApi.register(form.value.username, form.value.password, form.value.invite_code.trim());
    auth.setSession(data.token, data.user);
    ElMessage.success({ message: '注册成功', duration: 2000, showClose: true });
    router.push('/');
  } catch (e) {
    if (e instanceof ApiError && e.code === 'INVITE_CODE_INVALID') {
      ElMessage.error({ message: '邀请码无效、已使用或已过期', duration: 2000, showClose: true });
    }
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="auth-wrap">
    <el-card class="auth-card" shadow="never">
      <div class="brand-row">
        <el-icon size="32" color="var(--wt-primary)"><Reading /></el-icon>
        <h1>注册账号</h1>
      </div>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="3-20 位字母/数字/下划线/中文" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password placeholder="至少 8 位，含字母和数字" />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="form.confirm" type="password" show-password placeholder="再次输入密码" />
        </el-form-item>
        <el-form-item label="邀请码">
          <el-input v-model="form.invite_code" placeholder="必填，一码仅可注册一个账号" />
        </el-form-item>
        <el-button type="primary" class="submit-btn" :loading="loading" native-type="submit">注册</el-button>
      </el-form>
      <div class="auth-links">
        <router-link to="/login">已有账号？去登录</router-link>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.auth-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--wt-bg);
  padding: 16px;
}
.auth-card {
  width: 420px;
  max-width: 100%;
}
.brand-row {
  text-align: center;
  margin-bottom: 20px;
}
.brand-row h1 {
  margin: 8px 0 0;
  font-size: 22px;
  color: var(--wt-text);
}
.submit-btn {
  width: 100%;
}
.auth-links {
  margin-top: 16px;
  text-align: center;
  font-size: 13px;
}
</style>
