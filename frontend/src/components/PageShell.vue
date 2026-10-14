<script setup lang="ts">
import { useRouter } from 'vue-router';

const props = defineProps<{
  title?: string;
  subtitle?: string;
  showBack?: boolean;
}>();

const router = useRouter();

function goBack() {
  router.back();
}
</script>

<template>
  <div class="page-shell">
    <main class="page-main">
      <div class="page-header" v-if="title">
        <div class="header-left">
          <el-button v-if="showBack" text circle @click="goBack">
            <el-icon><ArrowLeft /></el-icon>
          </el-button>
          <div>
            <h1 class="page-title">{{ title }}</h1>
            <p v-if="subtitle" class="page-subtitle">{{ subtitle }}</p>
          </div>
        </div>
        <div class="header-right">
          <slot name="actions" />
        </div>
      </div>
      <slot />
    </main>
  </div>
</template>

<style scoped>
.page-shell {
  /* 扣掉顶栏高度：顶栏 sticky 仍占文档流，若这里用 100vh 则总高恒为 100vh+60px，
     内容再少也能滚一段、页面标题被顶栏压住。内容超出时仍能正常滚动 */
  min-height: calc(100vh - var(--wt-nav-h, 60px));
  min-height: calc(100dvh - var(--wt-nav-h, 60px));
  background: var(--wt-bg);
}
.page-main {
  max-width: 1200px;
  margin: 0 auto;
  padding: 24px 16px 48px;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  gap: 16px;
  flex-wrap: wrap;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--wt-text);
}
.page-subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--wt-text-3);
}
.header-right {
  display: flex;
  gap: 8px;
}
</style>
