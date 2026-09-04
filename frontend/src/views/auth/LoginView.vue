<script setup lang="ts">
import { ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { ElMessage } from 'element-plus';
import { authApi } from '../../api';
import { useAuthStore } from '../../stores/auth';

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const form = ref({ username: '', password: '' });
const loading = ref(false);

async function submit() {
  if (!form.value.username || !form.value.password) {
    ElMessage.warning('请输入用户名和密码');
    return;
  }
  loading.value = true;
  try {
    const { data } = await authApi.login(form.value.username, form.value.password);
    auth.setSession(data.token, data.user);
    ElMessage.success('登录成功');
    router.push((route.query.redirect as string) || '/');
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="auth-wrap">
    <el-card class="auth-card" shadow="never">
      <div class="brand-row">
        <el-icon size="32" color="#2563EB"><Reading /></el-icon>
        <h1>WordType</h1>
        <p class="subtitle">单词学习与打字速度练习系统</p>
      </div>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="用户名" @keyup.enter="submit" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password placeholder="密码" @keyup.enter="submit" />
        </el-form-item>
        <el-button type="primary" class="submit-btn" :loading="loading" native-type="submit">登录</el-button>
      </el-form>
      <div class="auth-links">
        <router-link to="/register">没有账号？去注册</router-link>
      </div>
      <div class="admin-hint">管理员账号由部署时预置，如需管理权限请联系管理员</div>
    </el-card>
  </div>
</template>

<style scoped>
.auth-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f8fafc;
  padding: 16px;
}
.auth-card {
  width: 400px;
  max-width: 100%;
}
.brand-row {
  text-align: center;
  margin-bottom: 24px;
}
.brand-row h1 {
  margin: 8px 0 4px;
  font-size: 24px;
  color: #111827;
}
.subtitle {
  margin: 0;
  font-size: 13px;
  color: #6b7280;
}
.submit-btn {
  width: 100%;
}
.auth-links {
  margin-top: 16px;
  text-align: center;
  font-size: 13px;
}
.admin-hint {
  margin-top: 12px;
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
}
</style>
