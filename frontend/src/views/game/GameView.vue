<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { ElMessage } from 'element-plus';
import { VideoPause, VideoPlay } from '@element-plus/icons-vue';
import PageShell from '../../components/PageShell.vue';
import { booksApi, gameApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { judge } from '../../core/judge';

/** Canvas 下坠打字游戏：全屏沉浸暗色 · 一体化 HUD · 红线判定 · D27 防作弊结算。 */

const settings = useSettingsStore();
const books = ref<any[]>([]);
const bookId = ref<number | null>(null);
const chapters = ref<any[]>([]);
const chapterId = ref<number | null>(null);
const mode = ref<'endless' | 'timed'>('endless');
const difficulty = ref<'easy' | 'normal' | 'hard'>('normal');
const phase = ref<'idle' | 'playing' | 'over'>('idle');

const score = ref(0);
const combo = ref(0);
const maxCombo = ref(0);
const blood = ref(5);
const paused = ref(false);
const pausesLeft = ref(2);
const timeLeft = ref(180);
const finalResult = ref<any>(null);

// ---- 交互反馈状态 ----
const showHint = ref(false); // 开局提示浮层
const hurtFlash = ref(false); // 掉血红闪
const inputFlash = ref<'' | 'good' | 'bad'>(''); // 输入微光
const inputShake = ref(false); // 输错震动
let hintTimer = 0;
let flashTimer = 0;
let hurtTimer = 0;

const canvasRef = ref<HTMLCanvasElement>();
let ctx: CanvasRenderingContext2D | null = null;
let raf = 0;
let lastTs = 0;
let startTime = 0;
let elapsedBeforePause = 0;

interface Drop {
  word_id: number;
  spelling: string;
  x: number;
  y: number;
  speed: number;
  typed: string;
  flashUntil: number;
  wrongFlash: boolean;
  firstKeyMs: number | null;
  lastKeyMs: number;
}
let drops: Drop[] = [];
let session: any = null;
let spawnTimer = 0;
let elapsed = 0;
let destroyedLogs: any[] = [];
let landedLogs: any[] = [];

const W = 1080;
const H = 560;
const DANGER_Y = 505;

const DIFF = {
  easy: { base: 26, accel: 0.8, maxOnScreen: 2, spawnMs: 3400 },
  normal: { base: 46, accel: 1.6, maxOnScreen: 3, spawnMs: 2400 },
  hard: { base: 72, accel: 2.6, maxOnScreen: 4, spawnMs: 1600 },
};

const comboMult = computed(() => (combo.value >= 20 ? 3 : combo.value >= 15 ? 2.5 : combo.value >= 10 ? 2 : combo.value >= 5 ? 1.5 : 1));
const lockedWord = computed(() => drops.find((d) => d.typed.length > 0) ?? null);
const inputProgress = computed(() => (lockedWord.value ? lockedWord.value.typed.length / lockedWord.value.spelling.length : 0));
const mmss = computed(() => `${String(Math.floor(timeLeft.value / 60)).padStart(2, '0')}:${String(timeLeft.value % 60).padStart(2, '0')}`);
const diffLabel = computed(() => ({ easy: '简单', normal: '普通', hard: '困难' })[difficulty.value] ?? '普通');

// ---- 沉浸模式：仅对局中（playing）整页转深色、导航上收；配置/结算为常规浅色页 ----
watch(
  phase,
  (p) => {
    document.body.classList.toggle('game-dark', p === 'playing');
  },
  { immediate: true },
);

onMounted(async () => {
  window.addEventListener('keydown', onKeydown);
  await settings.load();
  const { data } = await booksApi.list('all');
  books.value = data.items;
  mode.value = settings.settings?.game_limited_mode ? 'timed' : 'endless';
  difficulty.value = (settings.settings?.game_difficulty as any) ?? 'normal';
});

onBeforeUnmount(() => {
  stopLoop();
  document.body.classList.remove('game-dark');
});

// 掉血 → 爱心区红闪
watch(blood, (nv, ov) => {
  if (nv < ov) {
    hurtFlash.value = true;
    clearTimeout(hurtTimer);
    hurtTimer = window.setTimeout(() => (hurtFlash.value = false), 600);
  }
});

async function pickBook(id: number) {
  bookId.value = id;
  chapterId.value = null;
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
}

async function startGame() {
  if (!bookId.value) {
    ElMessage.warning('请先选择词库再开始游戏');
    return;
  }
  const chapterIds = chapterId.value ? [chapterId.value] : [];
  try {
    const { data } = await gameApi.start(chapterIds, difficulty.value, mode.value);
    session = data;
  } catch {
    return; // EMPTY_WORD_POOL 等由拦截器提示
  }
  drops = [];
  score.value = 0;
  combo.value = 0;
  maxCombo.value = 0;
  blood.value = 5;
  paused.value = false;
  pausesLeft.value = 2;
  timeLeft.value = 180;
  spawnTimer = 600;
  elapsed = 0;
  elapsedBeforePause = 0;
  destroyedLogs = [];
  landedLogs = [];
  startTime = performance.now();
  phase.value = 'playing';
  finalResult.value = null;
  inputFlash.value = '';
  inputShake.value = false;
  await nextTick(); // 对局画布为 v-if 分支挂载，等待 DOM 更新后再取上下文
  ctx = canvasRef.value?.getContext('2d') ?? null;
  // 开局提示：中央半透明浮层 1.5s 自动淡出
  showHint.value = true;
  clearTimeout(hintTimer);
  hintTimer = window.setTimeout(() => (showHint.value = false), 1500);
  startLoop();
}

function startLoop() {
  cancelAnimationFrame(raf);
  lastTs = performance.now();
  raf = requestAnimationFrame(loop);
}

function stopLoop() {
  cancelAnimationFrame(raf);
}

function curDiff() {
  return DIFF[difficulty.value] ?? DIFF.normal;
}

function loop(ts: number) {
  raf = requestAnimationFrame(loop);
  const dt = Math.min(50, ts - lastTs);
  lastTs = ts;
  if (paused.value) {
    render();
    return;
  }
  elapsed += dt;
  const diff = curDiff();
  // 含暂停前累计：暂停不清零，速度加成与限时倒计时不得回退
  const totalMs = elapsedBeforePause + elapsed;

  if (session.mode === 'timed') {
    timeLeft.value = Math.max(0, 180 - Math.floor(totalMs / 1000));
    if (timeLeft.value <= 0) return settle();
  }

  // 同屏词数随时间增加（1→3/4）
  spawnTimer -= dt;
  const maxOn = Math.min(diff.maxOnScreen, 1 + Math.floor(totalMs / 25000));
  if (spawnTimer <= 0 && drops.length < maxOn) {
    const used = new Set(drops.map((d) => d.word_id));
    const avail = session.words.filter((w: any) => !used.has(w.word_id) && !landedLogs.some((l) => l.word_id === w.word_id));
    if (avail.length) {
      const w = avail[Math.floor(Math.random() * avail.length)];
      drops.push({
        word_id: w.word_id,
        spelling: w.spelling,
        x: 80 + Math.random() * (W - 160),
        y: 40,
        speed: diff.base,
        typed: '',
        flashUntil: 0,
        wrongFlash: false,
        firstKeyMs: null,
        lastKeyMs: 0,
      });
    }
    spawnTimer = diff.spawnMs;
  }

  const speedMul = 1 + Math.floor(totalMs / 15000) * (diff.accel / 10);
  for (const d of drops) d.y += (d.speed * speedMul * dt) / 1000;

  // 触线落地 → 扣心
  const landed = drops.filter((d) => d.y >= DANGER_Y);
  for (const d of landed) {
    drops = drops.filter((x) => x !== d);
    landedLogs.push({ word_id: d.word_id, typed: d.typed, first_key_ms: d.firstKeyMs ?? 0, last_key_ms: d.lastKeyMs, destroyed: false, landed: true });
    combo.value = 0;
    blood.value -= 1;
    if (blood.value <= 0) {
      settle();
      return;
    }
  }
  render();
}

function onKeydown(e: KeyboardEvent) {
  if (phase.value !== 'playing') return;
  if (e.key === 'Escape') return togglePause();
  if (paused.value) return;
  const started = drops.filter((d) => d.typed.length > 0);
  const target = started.sort((a, b) => b.y - a.y)[0] ?? null;

  if (e.key === 'Backspace') {
    if (target) target.typed = target.typed.slice(0, -1);
    e.preventDefault();
    return;
  }
  if (e.key.length !== 1 || !/[a-zA-Z-]/.test(e.key)) return;
  e.preventDefault();

  let word = target;
  let isNew = false;
  if (!word) {
    // 首字母锁定：以该字母开头的未锁定词，取离红线最近者
    const candidates = drops.filter((d) => d.typed.length === 0 && d.spelling[0].toLowerCase() === e.key.toLowerCase());
    if (!candidates.length) return;
    word = candidates.sort((a, b) => b.y - a.y)[0];
    word.firstKeyMs = Math.round(performance.now() - startTime - elapsedBeforePause);
    isNew = true;
  }
  word.typed += e.key.toLowerCase();
  word.lastKeyMs = Math.round(performance.now() - startTime - elapsedBeforePause);

  // 逐键视觉/音效反馈：正确=绿光，错误=红光+轻震
  const expected = word.spelling[word.typed.length - 1] ?? '';
  const hit = isNew || expected.toLowerCase() === e.key.toLowerCase();
  flashInput(hit);
  playKeySound(hit);

  // 整词判定（无提交键）
  if (word.typed.length === word.spelling.length) {
    const loose = !!settings.settings?.loose_match;
    const result = judge(word.typed, word.spelling, loose);
    if (result === 'correct' || result === 'near') destroyWord(word);
    else rejectWord(word);
  }
}

// ---- 输入反馈：微光 + 震动 ----
function flashInput(ok: boolean) {
  inputFlash.value = ok ? 'good' : 'bad';
  clearTimeout(flashTimer);
  flashTimer = window.setTimeout(() => (inputFlash.value = ''), 180);
  if (!ok) {
    inputShake.value = false;
    requestAnimationFrame(() => (inputShake.value = true));
    window.setTimeout(() => (inputShake.value = false), 300);
  }
}

// ---- 按键音效（设置可关） ----
let audioCtx: AudioContext | null = null;
function playKeySound(ok: boolean) {
  if (!settings.settings?.game_key_sound) return;
  try {
    audioCtx ??= new AudioContext();
    if (audioCtx.state === 'suspended') void audioCtx.resume();
    const t = audioCtx.currentTime;
    const o = audioCtx.createOscillator();
    const g = audioCtx.createGain();
    o.type = 'square';
    o.frequency.value = ok ? 880 : 200;
    g.gain.setValueAtTime(0.035, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.08);
    o.connect(g).connect(audioCtx.destination);
    o.start(t);
    o.stop(t + 0.09);
  } catch {
    /* 音频不可用时静默 */
  }
}

function destroyWord(d: Drop) {
  destroyedLogs.push({
    word_id: d.word_id,
    typed: d.typed,
    first_key_ms: d.firstKeyMs ?? 0,
    last_key_ms: d.lastKeyMs,
    destroyed: true,
    landed: false,
  });
  // 加分 = (10 + 词长×2 + 高度加成) × Combo 倍率（口径19）
  combo.value += 1;
  maxCombo.value = Math.max(maxCombo.value, combo.value);
  const heightBonus = Math.round((10 * (DANGER_Y - d.y)) / DANGER_Y);
  score.value += Math.round((10 + d.spelling.length * 2 + heightBonus) * comboMult.value);
  drops = drops.filter((x) => x !== d);
}

function rejectWord(d: Drop) {
  // 整词敲错：扣心 + 弹回顶部 + Combo 清零
  blood.value -= 1;
  combo.value = 0;
  d.flashUntil = performance.now() + 300;
  d.wrongFlash = true;
  d.y = 40;
  d.typed = '';
  d.firstKeyMs = null;
  if (blood.value <= 0) settle();
}

function togglePause() {
  if (phase.value !== 'playing') return;
  if (!paused.value && pausesLeft.value <= 0) {
    ElMessage.warning('每局暂停次数已用完');
    return;
  }
  paused.value = !paused.value;
  if (paused.value) {
    pausesLeft.value -= 1;
    elapsedBeforePause += elapsed;
    elapsed = 0;
  } else {
    lastTs = performance.now();
  }
}

function resumeGame() {
  paused.value = false;
  lastTs = performance.now();
}

async function settle() {
  stopLoop();
  paused.value = false;
  phase.value = 'over';
  try {
    const { data } = await gameApi.submit(
      {
        session_id: session.session_id,
        difficulty: session.difficulty ?? difficulty.value,
        mode: session.mode ?? mode.value,
        duration_ms: Math.round(elapsedBeforePause + elapsed),
        score: score.value,
        max_combo: maxCombo.value,
        correct_count: destroyedLogs.length,
        words: destroyedLogs.concat(landedLogs),
      },
      uuid(),
    );
    finalResult.value = data;
  } catch {
    /* cheat/expired 由拦截器提示 */
  }
}

function render() {
  if (!ctx) return;
  const now = performance.now();
  // 背景（与页面深色渐变衔接）
  ctx.fillStyle = '#14171B';
  ctx.fillRect(0, 0, W, H);
  const grad = ctx.createLinearGradient(0, H - 140, 0, H);
  grad.addColorStop(0, 'rgba(255,77,79,0)');
  grad.addColorStop(1, 'rgba(255,77,79,0.28)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, H - 140, W, 140);

  // 危险接近度：有词接近时红线红光呼吸增强
  const maxWarn = drops.reduce((m, d) => Math.max(m, Math.min(1, d.y / DANGER_Y)), 0);
  const breathe = 0.5 + 0.25 * Math.sin(now / 280);
  const lineAlpha = Math.min(1, breathe + maxWarn * 0.4);
  ctx.strokeStyle = `rgba(255,77,79,${lineAlpha})`;
  ctx.lineWidth = 2 + maxWarn * 1.5;
  ctx.shadowColor = `rgba(255,77,79,${0.4 + maxWarn * 0.5})`;
  ctx.shadowBlur = 8 + maxWarn * 14;
  ctx.setLineDash([10, 8]);
  ctx.beginPath();
  ctx.moveTo(0, DANGER_Y);
  ctx.lineTo(W, DANGER_Y);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.shadowBlur = 0;

  // 下坠词：已敲金色 / 未敲白→红预警；错误闪烁红色
  ctx.textAlign = 'left';
  for (const d of drops) {
    const flashing = now < d.flashUntil;
    ctx.font = "bold 26px 'JetBrains Mono', Consolas, monospace";
    const total = ctx.measureText(d.spelling).width;
    let x = Math.min(Math.max(d.x, total / 2 + 10), W - total / 2 - 10) - total / 2;
    const warn = Math.max(0, Math.min(1, (d.y / DANGER_Y - 0.6) / 0.4));
    for (let i = 0; i < d.spelling.length; i++) {
      const ch = d.spelling[i];
      const typedCh = d.typed[i];
      if (flashing) ctx.fillStyle = '#ff4d4f';
      else if (typedCh != null) ctx.fillStyle = '#f5c542';
      else {
        // 未敲部分随接近底部由灰转红
        const g = Math.round(232 - (232 - 60) * warn);
        const b = Math.round(239 - (239 - 60) * warn);
        ctx.fillStyle = `rgb(255,${g},${b})`;
      }
      ctx.fillText(ch, x, d.y);
      x += ctx.measureText(ch).width;
    }
  }
}

/** 输入镜像字符颜色：正确=黄、错误=红、未输入=灰 */
function mirrorClass(i: number) {
  const w = lockedWord.value;
  if (!w) return '';
  if (i >= w.typed.length) return 'pending';
  return w.typed[i].toLowerCase() === w.spelling[i].toLowerCase() ? 'ok' : 'bad';
}
</script>

<template>
  <!-- 配置 / 结算：常规浅色页面 -->
  <PageShell v-if="phase !== 'playing'" title="游戏模式" subtitle="下坠打字 · Combo 阶梯 · 防作弊结算">
    <!-- 开局配置 -->
    <el-card v-if="phase === 'idle'" shadow="never" class="cfg-card">
      <el-form label-width="90px">
        <el-form-item label="词库">
          <el-select v-model="bookId" placeholder="选择词库" style="width: 220px" @change="pickBook">
            <el-option v-for="b in books" :key="b.id" :label="`${b.name}（${b.word_count} 词）`" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="章节">
          <el-select v-model="chapterId" placeholder="整本词库" clearable style="width: 220px">
            <el-option v-for="c in chapters" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="模式">
          <el-radio-group v-model="mode">
            <el-radio-button value="endless">无尽</el-radio-button>
            <el-radio-button value="timed">限时 3 分钟</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="难度">
          <el-radio-group v-model="difficulty">
            <el-radio-button value="easy">简单</el-radio-button>
            <el-radio-button value="normal">普通</el-radio-button>
            <el-radio-button value="hard">困难</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-button type="primary" size="large" :disabled="!bookId" @click="startGame">开始游戏</el-button>
        <p class="hint">输入首字母锁定单词 → 整词敲对销毁得分（连击 ≥5/10/15/20 倍率 1.5/2/2.5/3）；词触红线或整词敲错扣一心；5 心耗尽结束。Esc 暂停。难度决定初始下落速度、随时间加速幅度与同屏词数（简单 26px/s / 普通 46px/s / 困难 72px/s）。</p>
      </el-form>
    </el-card>

    <!-- 结算 -->
    <el-card v-else shadow="never" class="cfg-card">
      <el-result
        :icon="finalResult?.is_new_record ? 'success' : 'info'"
        :title="`本局得分：${finalResult?.score ?? score}`"
        :sub-title="finalResult ? `最高连击 ${maxCombo} · 历史最高 ${finalResult.max_score}${finalResult.is_new_record ? ' · 🎉新纪录！' : ''}` : '对局未入库（中断或校验失败）'"
      />
      <div class="hint-row">
        <el-button type="primary" @click="phase = 'idle'">返回配置</el-button>
        <el-button @click="startGame">再来一局</el-button>
      </div>
    </el-card>
  </PageShell>

  <!-- 对局：全屏沉浸深色 -->
  <div v-else class="game-page">
    <div class="game-stage">
      <div class="stage-wrap" :class="{ 'is-paused': paused }">
        <!-- 一体化悬浮状态栏 -->
        <div class="hud">
          <div class="hud-hearts" :class="{ hurt: hurtFlash }">
            <span v-for="i in 5" :key="i" class="heart" :class="{ off: i > blood }">{{ i <= blood ? '❤' : '♡' }}</span>
          </div>
          <div class="hud-center">
            <span class="hud-score">{{ score }}</span>
            <span v-if="combo >= 2" :key="combo" class="combo-pill">{{ combo }} Combo × {{ comboMult }}</span>
          </div>
          <div class="hud-right">
            <span class="hud-diff" :class="difficulty">{{ diffLabel }}</span>
            <span v-if="mode === 'timed'" class="hud-time">{{ mmss }}</span>
            <button class="icon-btn" :title="paused ? '继续' : `暂停（剩 ${pausesLeft} 次）`" @click="togglePause">
              <el-icon :size="20"><VideoPause v-if="!paused" /><VideoPlay v-else /></el-icon>
              <span class="badge">{{ pausesLeft }}</span>
            </button>
          </div>
        </div>

        <canvas ref="canvasRef" :width="W" :height="H" class="game-canvas"></canvas>

        <!-- 开局提示浮层 -->
        <Transition name="fade">
          <div v-if="showHint && !paused" class="start-hint">输入首字母锁定单词</div>
        </Transition>

        <!-- 暂停面板 -->
        <Transition name="fade">
          <div v-if="paused" class="pause-overlay">
            <div class="pause-panel">
              <h3>游戏暂停</h3>
              <p class="pause-sub">本局剩余暂停次数：{{ pausesLeft }}</p>
              <button class="pp-btn primary" @click="resumeGame">继续游戏</button>
              <button class="pp-btn" @click="startGame">重新开始</button>
              <button class="pp-btn danger" @click="settle">退出游戏</button>
            </div>
          </div>
        </Transition>
      </div>

      <!-- 底部输入条：锁定单词后才显示，逐字符高亮 -->
      <div v-if="lockedWord" class="input-bar" :class="[inputFlash, { shake: inputShake }]">
        <div class="word-mirror">
          <span v-for="(ch, i) in lockedWord.spelling.split('')" :key="i" class="mirror-ch" :class="mirrorClass(i)">{{ ch }}</span>
          <span class="caret">»</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: `${inputProgress * 100}%` }"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cfg-card {
  max-width: 640px;
}
.hint {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 12px;
}

/* ---- 对局区：全屏沉浸深色，85% 居中 ---- */
.game-page {
  min-height: 100vh;
  padding: 24px 0 48px;
  color: #e5e7eb;
}

/* ---- 对局区：85% 居中 ---- */
.game-stage {
  width: 85%;
  max-width: 1400px;
  margin: 0 auto;
}
.stage-wrap {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.45);
}
.stage-wrap.is-paused .game-canvas {
  filter: blur(6px) brightness(0.6);
}

/* ---- 一体化 HUD ---- */
.hud {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  background: linear-gradient(180deg, rgba(0, 0, 0, 0.55), rgba(0, 0, 0, 0));
  pointer-events: none;
}
.hud-hearts {
  display: flex;
  gap: 5px;
  font-size: 20px;
  min-width: 150px;
  border-radius: 8px;
  transition: background 0.2s;
}
.hud-hearts.hurt {
  animation: hurt-blink 0.6s ease;
}
@keyframes hurt-blink {
  0%, 100% { background: transparent; }
  25%, 75% { background: rgba(255, 77, 79, 0.35); }
  50% { background: rgba(255, 77, 79, 0.55); }
}
.heart {
  color: #ff4d4f;
  text-shadow: 0 0 6px rgba(255, 77, 79, 0.6);
  transition: opacity 0.2s, transform 0.2s;
}
.heart.off {
  color: rgba(255, 255, 255, 0.25);
  text-shadow: none;
  transform: scale(0.85);
}
.hud-center {
  display: flex;
  align-items: center;
  gap: 14px;
}
.hud-score {
  font-size: 34px;
  font-weight: 800;
  color: #fff;
  letter-spacing: 1px;
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.6);
  font-variant-numeric: tabular-nums;
}
.combo-pill {
  display: inline-block;
  background: rgba(245, 197, 66, 0.15);
  border: 1px solid rgba(245, 197, 66, 0.6);
  color: #f5c542;
  border-radius: 999px;
  padding: 3px 14px;
  font-size: 14px;
  font-weight: 700;
  animation: combo-pop 0.28s ease;
}
@keyframes combo-pop {
  0% { transform: scale(1); }
  45% { transform: scale(1.25); }
  100% { transform: scale(1); }
}
.hud-right {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 150px;
  justify-content: flex-end;
  pointer-events: auto;
}
.hud-diff {
  padding: 2px 12px;
  border-radius: 999px;
  border: 1px solid;
  font-size: 13px;
  font-weight: 700;
}
.hud-diff.easy {
  color: #52c41a;
  border-color: rgba(82, 196, 26, 0.6);
  background: rgba(82, 196, 26, 0.12);
}
.hud-diff.normal {
  color: #f5c542;
  border-color: rgba(245, 197, 66, 0.6);
  background: rgba(245, 197, 66, 0.12);
}
.hud-diff.hard {
  color: #ff4d4f;
  border-color: rgba(255, 77, 79, 0.6);
  background: rgba(255, 77, 79, 0.12);
}
.hud-time {
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.6);
}
.icon-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.35);
  color: #fff;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}
.icon-btn:hover {
  background: rgba(255, 255, 255, 0.15);
  border-color: rgba(255, 255, 255, 0.5);
}
.icon-btn .badge {
  position: absolute;
  right: -4px;
  top: -4px;
  min-width: 16px;
  height: 16px;
  line-height: 16px;
  border-radius: 8px;
  background: #f5c542;
  color: #1e212a;
  font-size: 11px;
  font-weight: 700;
  text-align: center;
  padding: 0 3px;
}
.game-canvas {
  width: 100%;
  display: block;
  background: #14171b;
  outline: none;
  transition: filter 0.25s ease;
}

/* ---- 开局提示浮层 ---- */
.start-hint {
  position: absolute;
  top: 42%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 6;
  padding: 14px 34px;
  border-radius: 12px;
  background: rgba(20, 23, 27, 0.72);
  border: 1px solid rgba(255, 255, 255, 0.15);
  color: #fff;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: 2px;
  pointer-events: none;
  backdrop-filter: blur(2px);
}
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.5s ease;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

/* ---- 暂停面板 ---- */
.pause-overlay {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(10, 12, 16, 0.35);
}
.pause-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  min-width: 260px;
  padding: 28px 36px;
  border-radius: 14px;
  background: rgba(30, 33, 42, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.12);
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.6);
}
.pause-panel h3 {
  margin: 0;
  color: #fff;
  font-size: 20px;
}
.pause-sub {
  margin: 0 0 6px;
  font-size: 12px;
  color: #9ca3af;
}
.pp-btn {
  width: 180px;
  padding: 10px 0;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  background: rgba(255, 255, 255, 0.06);
  color: #e5e7eb;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s;
}
.pp-btn:hover {
  background: rgba(255, 255, 255, 0.14);
  border-color: rgba(255, 255, 255, 0.4);
}
.pp-btn.primary {
  background: #2563eb;
  border-color: #2563eb;
  color: #fff;
}
.pp-btn.primary:hover {
  background: #1d4ed8;
}
.pp-btn.danger {
  border-color: rgba(255, 77, 79, 0.55);
  color: #ff6b6e;
}
.pp-btn.danger:hover {
  background: rgba(255, 77, 79, 0.15);
}

/* ---- 输入条：目标词逐字符高亮 ---- */
.input-bar {
  background: #0d0f12;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 10px;
  margin-top: 10px;
  padding: 16px 22px 18px;
  transition: box-shadow 0.12s ease, border-color 0.12s ease;
}
.input-bar.good {
  border-color: rgba(82, 196, 26, 0.7);
  box-shadow: 0 0 14px rgba(82, 196, 26, 0.35);
}
.input-bar.bad {
  border-color: rgba(255, 77, 79, 0.7);
  box-shadow: 0 0 14px rgba(255, 77, 79, 0.35);
}
.input-bar.shake {
  animation: input-shake 0.28s ease;
}
@keyframes input-shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-4px); }
  50% { transform: translateX(4px); }
  75% { transform: translateX(-2px); }
}
.word-mirror {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 26px;
  letter-spacing: 4px;
  min-height: 36px;
  display: flex;
  align-items: center;
}
.mirror-ch.ok {
  color: #f5c542;
}
.mirror-ch.bad {
  color: #ff4d4f;
}
.mirror-ch.pending {
  color: rgba(255, 255, 255, 0.35);
}
.caret {
  color: #f5c542;
  margin-left: 6px;
}
.progress-track {
  height: 4px;
  background: rgba(255, 255, 255, 0.12);
  border-radius: 2px;
  margin-top: 12px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: #f5c542;
  border-radius: 2px;
  transition: width 0.08s linear;
}

.hint-row {
  display: flex;
  justify-content: center;
  gap: 12px;
  margin-top: 12px;
}
</style>
