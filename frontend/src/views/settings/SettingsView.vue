<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { useSettingsStore, AppSettings } from '../../stores/settings';

const settings = useSettingsStore();
const form = ref<AppSettings | null>(null);
const saving = ref(false);

onMounted(async () => {
  form.value = { ...(await settings.load(true)) };
});

async function save() {
  if (!form.value) return;
  saving.value = true;
  try {
    const { server_date: _d, server_tz: _t, ...patch } = form.value;
    form.value = await settings.save(patch);
    ElMessage.success('设置已保存');
  } finally {
    saving.value = false;
  }
}

async function reset() {
  form.value = { ...(await settings.load(true)) };
}
</script>

<template>
  <PageShell title="设置" subtitle="每日额度 · 判定 · 复习形式 · 游戏/考核">
    <el-card v-if="form" shadow="never" class="cfg">
      <el-form label-width="180px">
        <el-divider content-position="left">每日额度</el-divider>
        <el-form-item label="每日新词上限（0–100）">
          <el-input-number v-model="form.daily_new_limit" :min="0" :max="100" />
        </el-form-item>
        <el-form-item label="每日复习上限（0–500）">
          <el-input-number v-model="form.daily_review_limit" :min="0" :max="500" />
        </el-form-item>

        <el-divider content-position="left">判定与练习</el-divider>
        <el-form-item label="宽松判定（Levenshtein ≤1 判近似）">
          <el-switch v-model="form.loose_match" :active-value="1" :inactive-value="0" />
        </el-form-item>
        <el-form-item label="指法引导">
          <el-switch v-model="form.typing_guide_on" :active-value="1" :inactive-value="0" />
        </el-form-item>
        <el-form-item label="TTS 发音">
          <el-switch v-model="form.tts_on" :active-value="1" :inactive-value="0" />
        </el-form-item>
        <el-form-item label="练习组规格（10–50）">
          <el-input-number v-model="form.practice_group_size" :min="10" :max="50" />
        </el-form-item>

        <el-divider content-position="left">复习</el-divider>
        <el-form-item label="复习形式">
          <el-radio-group v-model="form.review_form">
            <el-radio value="typing">打字默写</el-radio>
            <el-radio value="choice">选择</el-radio>
            <el-radio value="self">自评（D16 映射）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="答错当日重现一次（D11）">
          <el-switch v-model="form.review_wrong_reshow" :active-value="1" :inactive-value="0" />
        </el-form-item>

        <el-divider content-position="left">学习卡 / 游戏 / 考核</el-divider>
        <el-form-item label="默写卡展示秒数（2–5）">
          <el-input-number v-model="form.dictation_show_seconds" :min="2" :max="5" />
        </el-form-item>
        <el-form-item label="考核限时（分钟，5–120）">
          <el-input-number v-model="form.exam_time_limit" :min="5" :max="120" />
        </el-form-item>
        <el-form-item label="考核及格线">
          <el-input-number v-model="form.exam_pass_score" :min="0" :max="100" />
        </el-form-item>
        <el-form-item label="考核宽松判定">
          <el-switch v-model="form.exam_loose_match" :active-value="1" :inactive-value="0" />
        </el-form-item>
        <el-form-item label="游戏按键音效">
          <el-switch v-model="form.game_key_sound" :active-value="1" :inactive-value="0" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
          <el-button @click="reset">放弃修改</el-button>
        </el-form-item>
      </el-form>
      <p class="tz" v-if="form.server_tz">服务器时区 {{ form.server_tz }} · 服务器日期 {{ form.server_date }}（D13：切日以服务器为准）</p>
    </el-card>
  </PageShell>
</template>

<style scoped>
.cfg {
  max-width: 760px;
}
.tz {
  font-size: 12px;
  color: #9ca3af;
}
</style>
