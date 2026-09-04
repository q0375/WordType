<script setup lang="ts">
import AppNav from './components/AppNav.vue';
import { useRoute } from 'vue-router';
import { computed } from 'vue';

const route = useRoute();
const showNav = computed(() => !route.meta.guest);
</script>

<template>
  <AppNav v-if="showNav" />
  <router-view />
</template>

<style>
/* 游戏沉浸模式：整页深色渐变（由 GameView 在 body 上挂 game-dark 类控制） */
body.game-dark {
  background: linear-gradient(180deg, #1e212a 0%, #12141a 100%) fixed #12141a;
}
</style>
