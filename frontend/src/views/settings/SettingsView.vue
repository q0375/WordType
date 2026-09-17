<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { useSettingsStore, AppSettings } from '../../stores/settings';
import { aiConfigApi } from '../../api';

const settings = useSettingsStore();
const form = ref<AppSettings | null>(null);
const saving = ref(false);

// ---- 用户自有 AI（LLM）凭据 ----
const aiForm = ref({ api_base_url: '', api_key: '', model_name: '', temperature: 0.7, timeout_s: 30 });
const aiMasked = ref('');
const aiSaving = ref(false);
const aiTestResult = ref<any>(null);

onMounted(async () => {
  form.value = { ...(await settings.load(true)) };
  const { data } = await aiConfigApi.get();
  aiMasked.value = data.api_key_masked || '';
  aiForm.value = {
    api_base_url: data.api_base_url || '',
    api_key: '',
    model_name: data.model_name || '',
    temperature: data.temperature ?? 0.7,
    timeout_s: data.timeout_s ?? 30,
  };
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

async function saveAI() {
  aiSaving.value = true;
  try {
    const patch: any = { ...aiForm.value };
    if (!patch.api_key) delete patch.api_key; // 留空 = 不修改 Key
    const { data } = await aiConfigApi.put(patch);
    aiMasked.value = data.api_key_masked || '';
    aiForm.value.api_key = '';
    ElMessage.success('AI 模型配置已保存（仅本人可用，key 加密存储）');
  } finally {
    aiSaving.value = false;
  }
}

async function testAI() {
  aiTestResult.value = await aiConfigApi.test().then((r) => r.data);
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
            <el-radio value="self">自评（认识/模糊/不认识）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="答错当日重现一次">
          <el-switch v-model="form.review_wrong_reshow" :active-value="1" :inactive-value="0" />
        </el-form-item>
        <el-form-item label="复习时显示释义">
          <el-switch v-model="form.review_show_meaning" :active-value="1" :inactive-value="0" />
          <span class="field-hint">开启后默写时提示中文释义；关闭则为听发音默写</span>
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
        <el-form-item label="AI 建议">
          <el-radio-group v-model="form.advice_engine">
            <el-radio value="rule">规则引擎</el-radio>
            <el-radio value="llm">AI 模型</el-radio>
          </el-radio-group>
          <span class="field-hint">仪表盘「今日建议」由所选引擎生成；AI 模型不可用时自动回落规则引擎</span>
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存</el-button>
          <el-button @click="reset">放弃修改</el-button>
        </el-form-item>
      </el-form>
      <p class="tz" v-if="form.server_tz">服务器时区 {{ form.server_tz }} · 服务器日期 {{ form.server_date }}（日期切分以服务器时间为准）</p>
    </el-card>

    <!-- 用户自有 AI（LLM）凭据 -->
    <el-card shadow="never" class="cfg" style="margin-top: 16px">
      <el-form label-width="180px">
        <el-divider content-position="left">AI 模型配置（仅本人）</el-divider>
        <el-form-item label="API 基地址">
          <el-input v-model="aiForm.api_base_url" placeholder="https://api.example.com/v1（OpenAI 兼容）" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input
            v-model="aiForm.api_key"
            type="password"
            show-password
            :placeholder="aiMasked ? `已配置（${aiMasked}），留空则不修改` : '输入你的 Key'"
          />
        </el-form-item>
        <el-form-item label="模型名">
          <el-input v-model="aiForm.model_name" placeholder="gpt-4o-mini / deepseek-chat 等" />
        </el-form-item>
        <el-form-item label="温度">
          <el-input-number v-model="aiForm.temperature" :min="0" :max="2" :step="0.1" />
        </el-form-item>
        <el-form-item label="超时（秒）">
          <el-input-number v-model="aiForm.timeout_s" :min="1" :max="300" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="aiSaving" @click="saveAI">保存 AI 配置</el-button>
          <el-button @click="testAI">连通性测试</el-button>
        </el-form-item>
      </el-form>
      <el-alert
        v-if="aiTestResult"
        :type="aiTestResult.ok ? 'success' : 'error'"
        :closable="false"
        :title="`延迟 ${aiTestResult.latency_ms}ms · ${aiTestResult.message}`"
      />
      <p class="tz">凭据只属于你自己的账号（key 加密存储，永不回显明文）；在上方「AI 建议」选择「AI 模型」即用此凭据生成个性化建议，调用失败自动回落规则引擎。</p>
    </el-card>
  </PageShell>
</template>

<style scoped>
.cfg {
  max-width: 760px;
}
.tz {
  font-size: 12px;
  color: var(--wt-text-4);
}
.field-hint {
  font-size: 12px;
  color: var(--wt-text-4);
  margin-left: 12px;
}
</style>
