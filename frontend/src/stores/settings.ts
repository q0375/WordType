import { defineStore } from 'pinia';
import { ref } from 'vue';
import { settingsApi } from '../api';

export interface AppSettings {
  daily_review_limit: number;
  typing_guide_on: number;
  tts_on: number;
  review_form: 'typing' | 'choice' | 'self';
  dictation_show_seconds: number;
  practice_group_size: number;
  game_difficulty: 'easy' | 'normal' | 'hard';
  game_limited_mode: number;
  game_key_sound: number;
  // 下坠时间已固化到客户端（5s + 0.75s/字母 × 难度系数），不再是用户设置项
  exam_time_limit: number;
  exam_pass_score: number;
  // exam_loose_match（2026-09-19）与 loose_match（2026-09-20）均已取消：
  // 考核按词序相似度（difflib）连续计分；学习/练习/复习容差固定 1，均无开关可配
  review_wrong_reshow: number;
  review_show_meaning: number;
  advice_engine: 'rule' | 'llm';
  // 全局口径（仅管理员可改）：游戏下坠倍数，出现→消失 = 时限 × N，默认 2
  game_fall_duration_mult: number;
  server_date?: string;
  server_tz?: string;
}

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref<AppSettings | null>(null);

  async function load(force = false) {
    if (!settings.value || force) {
      const { data } = await settingsApi.get();
      settings.value = data;
    }
    return settings.value!;
  }

  async function save(patch: Partial<AppSettings>) {
    const { data } = await settingsApi.put(patch);
    settings.value = data;
    return settings.value!;
  }

  return { settings, load, save };
});
