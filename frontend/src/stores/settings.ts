import { defineStore } from 'pinia';
import { ref } from 'vue';
import { settingsApi } from '../api';

export interface AppSettings {
  daily_new_limit: number;
  daily_review_limit: number;
  loose_match: number;
  typing_guide_on: number;
  tts_on: number;
  review_form: 'typing' | 'choice' | 'self';
  dictation_show_seconds: number;
  practice_group_size: number;
  game_difficulty: 'easy' | 'normal' | 'hard';
  game_limited_mode: number;
  game_key_sound: number;
  exam_time_limit: number;
  exam_pass_score: number;
  exam_loose_match: number;
  review_wrong_reshow: number;
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
