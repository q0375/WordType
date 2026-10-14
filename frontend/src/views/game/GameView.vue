<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { ElMessage } from 'element-plus';
import { VideoPause, VideoPlay } from '@element-plus/icons-vue';
import PageShell from '../../components/PageShell.vue';
import { booksApi, gameApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';
import { uuid } from '../../core/idempotency';
import { partialRatio } from '../../core/judge';
import {
  DIFF,
  HP_MAX,
  TIMED_MODE_MS,
  comboMultiplier,
  damageOf,
  fallDurationSeconds,
  fallPenalty,
  fallSeconds,
  wordScore,
} from '../../core/gameRules';
import { isForbiddenKey } from '../../core/anticheat';

/** Canvas 下坠打字游戏：全屏沉浸暗色 · 一体化 HUD · 红线判定 · D27 防作弊结算。 */

// ---- 计分口径：常量与公式全部来自 core/gameRules.ts（与后端同源，勿单侧修改）----

const settings = useSettingsStore();
const books = ref<any[]>([]);
const bookId = ref<number | null>(null);
const chapters = ref<any[]>([]);
const chapterIds = ref<number[]>([]);
// 全选（与考核端同构）：不选任何章节 = 整本词库；显式全选 = 全部章节多选（词池与整本一致，同入整本榜）。
// 排行榜分组（2026-09-23 新口径）：按所选章节集合分组——单选=章节榜，多选=该组合专属榜，全选/不选=整本榜。
const allChaptersSelected = computed(() => chapters.value.length > 0 && chapterIds.value.length === chapters.value.length);
const someChaptersSelected = computed(() => chapterIds.value.length > 0 && !allChaptersSelected.value);
function toggleAllChapters(v: string | number | boolean) {
  chapterIds.value = v ? chapters.value.map((c: any) => c.id) : [];
}
const mode = ref<'endless' | 'timed'>('endless');
const difficulty = ref<'easy' | 'normal' | 'hard'>('normal');
// 计分时限已固化：ceil(字母数 / 3 / 难度系数)，系数 简单 0.8 / 普通 1.0 / 困难 1.25；
// 下坠（出现→消失）= 时限 × 下坠倍数（管理员全局可配，默认 2；gameRules.fallDurationSeconds 内做缺省兜底）
const phase = ref<'idle' | 'playing' | 'over'>('idle');

const score = ref(0);
const combo = ref(0);
const maxCombo = ref(0);
const hp = ref(HP_MAX); // 血条：500 点，扣 (1-得分比)×100
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
let imeHintShown = false; // 中文输入法截留提示，每局只提示一次

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
  fallS: number; // 该题时限（计分权重「得分比 × 时限」用；下坠动画时长 = 时限 × 全局倍数，见 spawnSpeed）
  typed: string;
  flashUntil: number;
  wrongFlash: boolean;
  firstKeyMs: number | null;
  lastKeyMs: number;
}
const drops = ref<Drop[]>([]); // 深层响应式：lockedWord/底部输入条依赖它，普通数组不会触发更新
/** 销毁结果浮层：全对=金「完美 +N」/ 有错字符=橙「NN% +N」/ 漏词落地=红「−N」（得分比连续值，不再分档） */
const popups = ref<{ id: number; text: string; cls: 'good' | 'near' | 'bad'; x: number; y: number }[]>([]);
let popupSeq = 0;
let session: any = null;
let spawnTimer = 0;
let elapsed = 0;
let destroyedLogs: any[] = [];
let landedLogs: any[] = [];

const W = 1080;
const H = 560;
const DANGER_Y = 505;

const comboMult = computed(() => comboMultiplier(combo.value));
const hpLevel = computed(() => {
  const p = hp.value / HP_MAX;
  return p > 0.5 ? 'ok' : p > 0.2 ? 'warn' : 'danger';
});
const lockedWord = computed(() => drops.value.find((d) => d.typed.length > 0) ?? null);
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
  const { data } = await booksApi.list('all', '', 1, 500);
  books.value = data.items;
  mode.value = settings.settings?.game_limited_mode ? 'timed' : 'endless';
  difficulty.value = (settings.settings?.game_difficulty as any) ?? 'normal';
});

onBeforeUnmount(() => {
  stopLoop();
  // 关键：移除 window 级监听，否则离开游戏后 phase 仍为 playing 时
  // 该 handler 会全局拦截字母/退格（preventDefault），导致其他页面输入异常
  window.removeEventListener('keydown', onKeydown);
  document.body.classList.remove('game-dark');
});

// 掉血 → 血条区红闪
watch(hp, (nv, ov) => {
  if (nv < ov) {
    hurtFlash.value = true;
    clearTimeout(hurtTimer);
    hurtTimer = window.setTimeout(() => (hurtFlash.value = false), 600);
  }
});

async function pickBook(id: number) {
  bookId.value = id;
  chapterIds.value = [];
  const { data } = await booksApi.chapters(id);
  chapters.value = data.items;
}

// ---- 排行榜（仅公共词库开放）----
const leaderboard = ref<any>(null);
const lbLoading = ref(false);
const curBook = computed(() => books.value.find((b) => b.id === bookId.value));
const isPublicBook = computed(() => !!curBook.value?.is_public);

const lbScopeText = computed(() => {
  const n = chapterIds.value.length;
  // 分组文案与后端 chapter_key 口径一致：全选=整本（词池相同）；单选=章节名；多选=组合专属榜
  const chText =
    n === 0 || allChaptersSelected.value
      ? '整本词库'
      : n === 1
        ? chapters.value.find((c: any) => c.id === chapterIds.value[0])?.name
        : `${n} 章组合`;
  return [curBook.value?.name, chText, mode.value === 'timed' ? '限时 3 分钟' : '无尽', diffLabel.value]
    .filter(Boolean)
    .join(' · ');
});

function medal(rank: number) {
  return rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : String(rank);
}
function lbRowClass({ row }: { row: any }) {
  return row.is_me ? 'lb-me' : '';
}

async function loadLeaderboard() {
  if (!bookId.value || !isPublicBook.value) {
    leaderboard.value = null;
    return;
  }
  lbLoading.value = true;
  try {
    const { data } = await gameApi.leaderboard({
      book_id: bookId.value,
      // 按所选章节集合查对应榜（2026-09-23 新口径）：空=整本榜；后端会把「选满全部章节」归一为整本榜
      chapter_ids: chapterIds.value.length ? chapterIds.value.join(',') : undefined,
      mode: mode.value,
      difficulty: difficulty.value,
    });
    leaderboard.value = data;
  } catch {
    leaderboard.value = null;
  } finally {
    lbLoading.value = false;
  }
}

// 词库/章节/模式/难度任一变化即刷新对应榜
watch([bookId, chapterIds, mode, difficulty], () => {
  if (phase.value === 'idle') void loadLeaderboard();
});
// 结算返回配置页后刷新，展示本局最新名次
watch(phase, (p) => {
  if (p === 'idle') void loadLeaderboard();
});

async function startGame() {
  if (!bookId.value) {
    ElMessage.warning({ message: '请先选择词库再开始游戏', duration: 2000, showClose: true });
    return;
  }
  // 未选章节时传所选词库的全部章节——否则空数组会被后端当作"全库抽词"，所选词库不生效；
  // 后端会把「选满全部章节」归一为整本局（chapter_key=NULL），与不选同榜
  const ids = chapterIds.value.length ? chapterIds.value : chapters.value.map((c) => c.id);
  try {
    // book_id 一并上报；排行榜分组键 chapter_key 由后端按 ids 规范化（开局固化，submit 不可伪造）
    const { data } = await gameApi.start(ids, difficulty.value, mode.value, bookId.value);
    session = data;
  } catch {
    return; // EMPTY_WORD_POOL 等由拦截器提示
  }
  drops.value = [];
  score.value = 0;
  combo.value = 0;
  maxCombo.value = 0;
  hp.value = HP_MAX;
  paused.value = false;
  panelOpen.value = false;
  pausesLeft.value = 2;
  timeLeft.value = 180;
  spawnTimer = 600;
  elapsed = 0;
  elapsedBeforePause = 0;
  destroyedLogs = [];
  landedLogs = [];
  imeHintShown = false;
  startTime = performance.now();
  phase.value = 'playing';
  finalResult.value = null;
  inputFlash.value = '';
  inputShake.value = false;
  // 清掉上一局的一次性 UI 定时器：上局结束瞬间触发的受伤闪红/输入闪烁可能在新区开头误闪
  clearTimeout(hurtTimer);
  clearTimeout(flashTimer);
  hurtFlash.value = false;
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

// 下坠节奏：速度 = 落距 ÷ 下坠时长，时长 = 时限 × 下坠倍数（管理员全局配置，缺省 2）；
// 计分权重仍是时限本身（fallS = fallSeconds）

function spawnSpeed(w: { spelling: string }): number {
  const dist = DANGER_Y - 40; // 出生点 → 红线
  return dist / fallDurationSeconds(w.spelling, difficulty.value, settings.settings?.game_fall_duration_mult);
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
    timeLeft.value = Math.max(0, TIMED_MODE_MS / 1000 - Math.floor(totalMs / 1000));
    if (timeLeft.value <= 0) return settle();
  }

  // 同屏词数随时间增加（1→3/4）
  spawnTimer -= dt;
  const maxOn = Math.min(diff.maxOnScreen, 1 + Math.floor(totalMs / 25000));
  if (spawnTimer <= 0 && drops.value.length < maxOn) {
    const used = new Set(drops.value.map((d) => d.word_id));
    // 局内一轮不放回（2026-09-23）：同屏 + 已落地 + 已打掉的词都不再抽，所选范围全部词
    // 都用过一轮后才进入第二轮——修掉「打掉过的词反复再掉」造成的局内重复（打越快重复越快）。
    // 词池 = 开局后端整池洗牌下发的所选范围全部词（不再锁 30 个），范围大时整局自然无重复。
    let avail = session.words.filter(
      (w: any) =>
        !used.has(w.word_id) &&
        !landedLogs.some((l) => l.word_id === w.word_id) &&
        !destroyedLogs.some((l) => l.word_id === w.word_id),
    );
    if (!avail.length) {
      avail = session.words.filter((w: any) => !used.has(w.word_id) && !landedLogs.some((l) => l.word_id === w.word_id));
    }
    if (avail.length) {
      const w = avail[Math.floor(Math.random() * avail.length)];
      drops.value.push({
        word_id: w.word_id,
        spelling: w.spelling,
        x: 80 + Math.random() * (W - 160),
        y: 40,
        speed: spawnSpeed(w),
        fallS: fallSeconds(w.spelling, difficulty.value),
        typed: '',
        flashUntil: 0,
        wrongFlash: false,
        firstKeyMs: null,
        lastKeyMs: 0,
      });
    }
    spawnTimer = diff.spawnMs;
  }

  // 不下坠加速：每个词「出现 → 消失」恒等于 时限 × 2（速度 = 落距 ÷ 下坠时长）
  for (const d of drops.value) d.y += (d.speed * dt) / 1000;

  // 触线落地 → 按已输入的正确占比结算（得分比 × 时限计分，扣血 =(1-得分比)×100）
  const landed = drops.value.filter((d) => d.y >= DANGER_Y);
  for (const d of landed) {
    drops.value = drops.value.filter((x) => x !== d);
    const ratio = partialRatio(d.typed, d.spelling);
    landedLogs.push({
      word_id: d.word_id,
      typed: d.typed,
      ratio,
      limit_s: d.fallS,
      first_key_ms: d.firstKeyMs ?? 0,
      last_key_ms: d.lastKeyMs,
      destroyed: false,
      landed: true,
    });
    combo.value = 0;
    // 漏词扣分（2026-09-20）：保留已输入部分分，另按缺口扣 ceil((1-得分比)×时限)，后端 recompute 同式复核
    const penalty = fallPenalty(ratio, d.fallS);
    score.value += wordScore(ratio, d.fallS) - penalty;
    hp.value -= damageOf(ratio);
    // 惩罚浮层：红色「−N」让漏词代价在词的位置可见
    const id = ++popupSeq;
    popups.value.push({ id, text: `-${penalty}`, cls: 'bad', x: d.x, y: d.y });
    window.setTimeout(() => (popups.value = popups.value.filter((p) => p.id !== id)), 950);
    if (hp.value <= 0) {
      settle();
      return;
    }
  }
  render();
}

// ---- 键盘输入：仅放行 ASCII 可打印字符 ----
// 1) 全角中文标点（（ ＝ ； 等）码位 > 0x7E 一律拒绝，永远不会进入输入缓冲；
// 2) 中文输入法在中文模式下会截留按键（e.key === 'Process'），此时按物理键位（e.code）还原字符，开着中文输入法也能打。
const CODE_CHAR: Record<string, string> = (() => {
  const m: Record<string, string> = {
    Space: ' ', Minus: '-', Equal: '=', Period: '.', Comma: ',', Slash: '/',
    Semicolon: ';', Quote: "'", BracketLeft: '[', BracketRight: ']', Backslash: '\\',
  };
  for (let i = 0; i < 26; i++) m[`Key${String.fromCharCode(65 + i)}`] = String.fromCharCode(97 + i);
  for (let i = 0; i < 10; i++) m[`Digit${i}`] = String(i);
  return m;
})();

function keyToChar(e: KeyboardEvent): string | null {
  if (e.key === 'Process') return CODE_CHAR[e.code] ?? null; // 输入法截留：按物理键还原
  if (e.key.length !== 1) return null;
  const cp = e.key.codePointAt(0) ?? 0;
  return cp >= 0x20 && cp <= 0x7e ? e.key : null; // 仅 ASCII 可打印（含空格/撇号/等号等符号词字符）
}

function onKeydown(e: KeyboardEvent) {
  if (phase.value !== 'playing') return;
  if (e.key === 'Escape') return togglePause();
  if (panelOpen.value) {
    // 面板打开：↑↓/←→ 在三个选项间移动并高亮；其余按键不传给游戏（背景不再被打字）
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
      e.preventDefault();
      ppMove(1);
      return;
    }
    if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
      e.preventDefault();
      ppMove(-1);
      return;
    }
    return; // Enter/空格由聚焦按钮原生响应
  }
  if (paused.value) return;
  if (isForbiddenKey(e)) {
    // 防作弊（D37）：Ctrl/Alt/Insert 等键按下即清空当前输入进度
    for (const d of drops.value) if (d.typed.length > 0) d.typed = '';
    ElMessage.warning({ message: '检测到禁用按键（Ctrl/Alt/Insert），输入已清空', duration: 2000, showClose: true });
    e.preventDefault();
    return;
  }
  if (e.key === 'Process' && !CODE_CHAR[e.code]) {
    // 输入法截留且无法从键位还原：提示切英文，每局只提示一次
    if (!imeHintShown) {
      imeHintShown = true;
      ElMessage.info({ message: '检测到中文输入法，建议切换到英文模式（通常按 Shift 切换）', duration: 2000, showClose: true });
    }
    return;
  }
  const started = drops.value.filter((d) => d.typed.length > 0);
  const target = started.sort((a, b) => b.y - a.y)[0] ?? null;

  // 中途放弃当前单词（Delete 或 Shift+Backspace）：词立即消失，按已输入比例结算
  if (e.key === 'Delete' || (e.key === 'Backspace' && e.shiftKey)) {
    if (target) {
      abandonWord(target);
      e.preventDefault();
    }
    return;
  }
  if (e.key === 'Backspace') {
    if (target) target.typed = target.typed.slice(0, -1);
    e.preventDefault();
    return;
  }
  const char = keyToChar(e);
  if (!char) return;
  e.preventDefault();

  let word = target;
  let isNew = false;
  if (!word) {
    // 首字母锁定：以该字母开头的未锁定词，取离红线最近者
    const candidates = drops.value.filter((d) => d.typed.length === 0 && d.spelling[0].toLowerCase() === char.toLowerCase());
    if (!candidates.length) return;
    word = candidates.sort((a, b) => b.y - a.y)[0];
    word.firstKeyMs = Math.round(performance.now() - startTime - elapsedBeforePause);
    isNew = true;
  }
  word.typed += char; // 原样存储（大小写敏感：大写字母会按错误字符计）
  word.lastKeyMs = Math.round(performance.now() - startTime - elapsedBeforePause);

  // 逐键视觉/音效反馈：正确=绿光，错误=红光+轻震（严格比较，大小写也算错）
  const expected = word.spelling[word.typed.length - 1] ?? '';
  const hit = isNew || expected === char;
  flashInput(hit);
  playKeySound(hit);

  // 整词结算（无提交键）：输入完成即按词序相似度销毁结算
  if (word.typed.length === word.spelling.length) destroyWord(word);
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

// ---- 得分比 / 掉血（新口径：分数 = ceil(得分比 × 时限 × Combo)；掉血 = (1-得分比) × 100）----
// 得分比 = core/judge.partialRatio（词序相似度，difflib 双端同源，大小写敏感）

function destroyWord(d: Drop) {
  // 输入完成即结算：得分比 = 词序相似度（core/judge.partialRatio，difflib 同源）
  const ratio = partialRatio(d.typed, d.spelling);
  destroyedLogs.push({
    word_id: d.word_id,
    typed: d.typed,
    ratio,
    limit_s: d.fallS,
    first_key_ms: d.firstKeyMs ?? 0,
    last_key_ms: d.lastKeyMs,
    destroyed: true,
    landed: false,
  });
  // 加分 = ceil(得分比 × 该词落地时限 × Combo 倍率)
  combo.value += 1;
  maxCombo.value = Math.max(maxCombo.value, combo.value);
  const gained = wordScore(ratio, d.fallS, comboMult.value);
  score.value += gained;
  // 未全对的销毁同样按缺口掉血（2026-09-20 补：此前只有触线落地/主动放弃掉血，
  // 「首字母敲对锁定 + 后面乱敲凑满长度」可零损耗销毁白嫖）；完美（ratio=1）不扣血。
  // combo 阶梯保持不动：后端 recompute_score 对所有 destroyed 一律 +1，两端口径一致
  if (ratio < 1) {
    hp.value -= damageOf(ratio);
    if (hp.value <= 0) {
      settle();
      return;
    }
  }
  // 结算浮层：全对金「完美」，有错字橙「命中百分比」（连续得分比，逐字差异一目了然）
  const id = ++popupSeq;
  popups.value.push({
    id,
    text: ratio >= 1 ? `完美 +${gained}` : `${Math.round(ratio * 100)}% +${gained}`,
    cls: ratio >= 1 ? 'good' : 'near',
    x: d.x,
    y: d.y,
  });
  window.setTimeout(() => (popups.value = popups.value.filter((p) => p.id !== id)), 950);
  drops.value = drops.value.filter((x) => x !== d);
}

/** 中途放弃（第 11 条）：词立即消失，得分比按当时输入了多少计算，Combo 清零 */
function abandonWord(d: Drop) {
  const ratio = partialRatio(d.typed, d.spelling);
  landedLogs.push({
    word_id: d.word_id,
    typed: d.typed,
    ratio,
    limit_s: d.fallS,
    first_key_ms: d.firstKeyMs ?? 0,
    last_key_ms: d.lastKeyMs,
    destroyed: false,
    landed: false,
  });
  score.value += wordScore(ratio, d.fallS);
  hp.value -= damageOf(ratio);
  combo.value = 0;
  drops.value = drops.value.filter((x) => x !== d);
  if (hp.value <= 0) settle();
}

const panelOpen = ref(false);

// 暂停面板三个选项的键盘导航（Esc 打开面板后用 ↑↓ 选择，回车/空格确认）
const ppIndex = ref(0);
const ppBtns = ref<(HTMLButtonElement | null)[]>([null, null, null]);
function setPpBtn(el: any, i: number) {
  if (el) ppBtns.value[i] = el as HTMLButtonElement;
}
function ppMove(step: number) {
  const n = ppBtns.value.filter(Boolean).length || 3;
  ppIndex.value = (ppIndex.value + step + n) % n;
  nextTick(() => ppBtns.value[ppIndex.value]?.focus());
}

function togglePause() {
  if (phase.value !== 'playing') return;
  if (panelOpen.value) {
    // 面板已打开：Esc/按钮 = 关闭面板并继续游戏
    closePanel();
    return;
  }
  if (pausesLeft.value > 0) {
    // 还有暂停次数：冻结时间与下坠
    paused.value = true;
    pausesLeft.value -= 1;
    elapsedBeforePause += elapsed;
    elapsed = 0;
  } else {
    ElMessage.warning({ message: '暂停次数已用完，游戏继续进行，可在面板中退出', duration: 2000, showClose: true });
  }
  panelOpen.value = true;
  // 打开即把焦点落在第一个选项上，↑↓ 可在三个选项间移动
  ppIndex.value = 0;
  nextTick(() => ppBtns.value[0]?.focus());
}

function closePanel() {
  panelOpen.value = false;
  if (paused.value) {
    paused.value = false;
    lastTs = performance.now();
  }
}

async function settle() {
  stopLoop();
  paused.value = false;
  panelOpen.value = false;
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
  const maxWarn = drops.value.reduce((m, d) => Math.max(m, Math.min(1, d.y / DANGER_Y)), 0);
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
  for (const d of drops.value) {
    const flashing = now < d.flashUntil;
    ctx.font = "bold 26px 'JetBrains Mono', Consolas, monospace";
    const total = ctx.measureText(d.spelling).width;
    let x = Math.min(Math.max(d.x, total / 2 + 10), W - total / 2 - 10) - total / 2;
    const warn = Math.max(0, Math.min(1, (d.y / DANGER_Y - 0.6) / 0.4));
    for (let i = 0; i < d.spelling.length; i++) {
      const ch = d.spelling[i];
      const typedCh = d.typed[i];
      const cw = ctx.measureText(ch).width;
      if (flashing) {
        ctx.fillStyle = '#ff4d4f';
        ctx.fillText(ch, x, d.y);
      } else if (typedCh != null) {
        if (typedCh === ch) {
          // 敲对：金色（大小写敏感）
          ctx.fillStyle = '#f5c542';
          ctx.fillText(ch, x, d.y);
        } else if (ch === ' ' || typedCh === ' ') {
          // 空格敲错：红色方块
          ctx.fillStyle = '#ff4d4f';
          ctx.fillRect(x, d.y - 19, Math.max(cw - 2, 12), 20);
        } else {
          // 敲错：显示玩家实际输入的字母，红色
          ctx.fillStyle = '#ff4d4f';
          ctx.fillText(typedCh, x, d.y);
        }
      } else {
        // 未敲部分随接近底部由灰转红
        const g = Math.round(232 - (232 - 60) * warn);
        const b = Math.round(239 - (239 - 60) * warn);
        ctx.fillStyle = `rgb(255,${g},${b})`;
        ctx.fillText(ch, x, d.y);
      }
      x += cw;
    }
  }
}

/** 输入镜像字符颜色：正确=黄、错误=红、未输入=灰（大小写敏感） */
function mirrorClass(i: number) {
  const w = lockedWord.value;
  if (!w) return '';
  if (i >= w.typed.length) return 'pending';
  return w.typed[i] === w.spelling[i] ? 'ok' : 'bad';
}
/** 该位置涉及空格错误（应为空格却输入了别的字符 / 输入成空格但应为字母）→ 渲染红色方块 */
function isBlockSlot(i: number) {
  const w = lockedWord.value;
  if (!w || i >= w.typed.length) return false;
  if (w.typed[i] === w.spelling[i]) return false;
  return w.spelling[i] === ' ' || w.typed[i] === ' ';
}
/** 已输入位置：正确显示目标字符，错误显示玩家实际敲入的字符 */
function mirrorChar(i: number, ch: string) {
  const w = lockedWord.value;
  if (!w) return ch;
  if (i < w.typed.length && w.typed[i] !== ch) {
    return w.typed[i] === ' ' ? '␣' : w.typed[i];
  }
  return ch === ' ' ? '␣' : ch;
}
</script>

<template>
  <!-- 配置 / 结算：常规浅色页面 -->
  <PageShell v-if="phase !== 'playing'" title="游戏模式" subtitle="下坠打字 · Combo 阶梯 · 防作弊结算">
    <!-- 配置 + 排行榜（排行榜仅对公共词库开放） -->
    <div v-if="phase === 'idle'" class="idle-grid">
    <el-card shadow="never" class="cfg-card">
      <el-form label-width="90px">
        <el-form-item label="词库">
          <el-select v-model="bookId" placeholder="选择词库" style="width: 220px" @change="pickBook">
            <el-option v-for="b in books" :key="b.id" :label="`${b.name}（${b.word_count} 词）`" :value="b.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="章节">
          <el-select v-model="chapterIds" multiple collapse-tags collapse-tags-tooltip placeholder="整本词库（可多选）" style="width: 220px">
            <template #header>
              <el-checkbox :model-value="allChaptersSelected" :indeterminate="someChaptersSelected" @change="toggleAllChapters">全选</el-checkbox>
            </template>
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
        <p class="hint">
          <span class="hl-title">玩法</span>输入首字母锁定单词 → <b>整词输入完成即按词序相似度结算</b>；<b>连击 ≥5/10/15/20 → 倍率 1.5/2/2.5/3</b>；词触红线或<b>未全对结算</b>按缺口掉血，<b>血条 500 点耗尽结束</b>。<b>Esc</b> 暂停，<b>Delete（或 Shift+退格）</b>放弃当前单词。<br />
          <span class="hl-title">计分</span>单词得分 = <b>ceil(得分比 × 该词时限 × 连击倍率)</b>；时限 = <b>ceil(字母数 ÷ 3 ÷ 难度系数)</b>（简单 0.8 / 普通 1.0 / 困难 1.25，系数越大时限越短）；每个词从出现到消失 = 时限 × <b>下坠倍数</b>（管理员在系统设置中全局配置，默认 2）。<br />
          <span class="hl-title">判词</span>得分比 = <b>词序相似度</b>（difflib 算法，连续值，如 5 个字母错 1 个 = 80%）；<b>区分大小写</b>（大写/小写算错误字符）。<b class="c-bad">敲错的字母显示红色</b>，空格敲错显示红色方块。<b>掉血</b>：(1 − 得分比) × 100；<b>漏词触红线同步扣分</b>：ceil((1 − 得分比) × 时限)，没碰的词扣 2~5 分。
        </p>
      </el-form>
    </el-card>

    <!-- 排行榜：仅公共词库；按 词库+章节+模式+难度 分组，同一玩家取最高分 -->
    <el-card shadow="never" class="lb-card">
      <template #header>
        <div class="lb-head">
          <span class="lb-title">排行榜</span>
          <span class="lb-scope" v-if="bookId">{{ lbScopeText }}</span>
          <el-button text size="small" :loading="lbLoading" @click="loadLeaderboard">刷新</el-button>
        </div>
      </template>

      <el-empty v-if="!bookId" description="选择词库后查看排行榜" :image-size="60" />
      <el-alert
        v-else-if="!isPublicBook"
        type="info"
        :closable="false"
        show-icon
        title="排行榜仅对公共词库开放"
        description="当前所选为私有词库，成绩不计入排行榜。"
      />
      <template v-else>
        <el-empty v-if="!leaderboard?.top?.length" description="该组合还没有成绩，快来抢第一" :image-size="60" />
        <template v-else>
          <div class="lb-sub">前 10 名</div>
          <el-table :data="leaderboard.top" size="small" :row-class-name="lbRowClass">
            <el-table-column label="排名" width="66">
              <template #default="{ row }"><span class="lb-rank">{{ medal(row.rank) }}</span></template>
            </el-table-column>
            <el-table-column label="玩家" show-overflow-tooltip>
              <template #default="{ row }">
                <span :class="{ 'lb-name-me': row.is_me }">{{ row.username }}<em v-if="row.is_me">（我）</em></span>
              </template>
            </el-table-column>
            <el-table-column prop="score" label="最高分" width="96" align="right" />
          </el-table>

          <div class="lb-sub">我的名次<span class="lb-count">共 {{ leaderboard.total_players }} 位玩家</span></div>
          <el-table v-if="leaderboard.around?.length" :data="leaderboard.around" size="small" :row-class-name="lbRowClass">
            <el-table-column label="排名" width="66">
              <template #default="{ row }"><span class="lb-rank">{{ medal(row.rank) }}</span></template>
            </el-table-column>
            <el-table-column label="玩家" show-overflow-tooltip>
              <template #default="{ row }">
                <span :class="{ 'lb-name-me': row.is_me }">{{ row.username }}<em v-if="row.is_me">（我）</em></span>
              </template>
            </el-table-column>
            <el-table-column prop="score" label="最高分" width="96" align="right" />
          </el-table>
          <el-alert
            v-else
            type="warning"
            :closable="false"
            show-icon
            title="你还没有该组合的成绩"
            description="玩一局即可上榜（同一玩家取最高分）"
          />
        </template>
      </template>
    </el-card>
    </div>

    <!-- 结算 -->
    <el-card v-else shadow="never" class="cfg-card">
      <el-result
        :icon="finalResult?.is_new_record ? 'success' : 'info'"
        :title="`本局得分：${finalResult?.score ?? score}`"
        :sub-title="finalResult
          ? `最高连击 ${maxCombo}${finalResult.hp_bonus ? ` · 生命奖励 +${finalResult.hp_bonus}（剩余生命）` : ''}${finalResult.is_new_record ? ' · 🎉新纪录！' : ''}`
          : '对局未入库（中断或校验失败）'"
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
          <div class="hud-hp" :class="[hpLevel, { hurt: hurtFlash }]">
            <div class="hp-track">
              <div class="hp-fill" :style="{ width: `${(hp / HP_MAX) * 100}%` }"></div>
            </div>
            <span class="hp-num">{{ hp }}<i>/{{ HP_MAX }}</i></span>
          </div>
          <div class="hud-center">
            <span class="hud-score">{{ score }}</span>
            <span v-if="combo >= 2" :key="combo" class="combo-pill">{{ combo }} Combo × {{ comboMult }}</span>
          </div>
          <div class="hud-right">
            <span class="hud-diff" :class="difficulty">{{ diffLabel }}</span>
            <span v-if="mode === 'timed'" class="hud-time">{{ mmss }}</span>
            <button class="icon-btn" :title="panelOpen ? '关闭面板并继续' : pausesLeft > 0 ? `暂停（剩 ${pausesLeft} 次）` : '打开面板（暂停已用完，可退出）'" @click="togglePause">
              <el-icon :size="20"><VideoPause v-if="!panelOpen" /><VideoPlay v-else /></el-icon>
              <span class="badge">{{ pausesLeft }}</span>
            </button>
          </div>
        </div>

        <canvas ref="canvasRef" :width="W" :height="H" class="game-canvas"></canvas>

        <!-- 销毁结果浮层：完美（金）/ 未全对（橙，展示得分比百分比） -->
        <span
          v-for="p in popups"
          :key="p.id"
          class="destroy-pop"
          :class="p.cls"
          :style="{ left: (p.x / W) * 100 + '%', top: (p.y / H) * 100 + '%' }"
          >{{ p.text }}</span
        >

        <!-- 开局提示浮层 -->
        <Transition name="fade">
          <div v-if="showHint && !paused" class="start-hint">英文输入法 · 首字母锁定单词</div>
        </Transition>

        <!-- 暂停面板：有次数=冻结游戏；次数用完=面板仍可打开（游戏继续）供退出 -->
        <Transition name="fade">
          <div v-if="panelOpen" class="pause-overlay">
            <div class="pause-panel">
              <h3>{{ paused ? '游戏暂停' : '暂停次数已用完' }}</h3>
              <p class="pause-sub">
                {{ paused ? `本局剩余暂停次数：${pausesLeft}` : '游戏仍在进行（时间与下坠继续），输入依然有效' }}
              </p>
              <button
                :ref="(el) => setPpBtn(el, 0)"
                class="pp-btn primary"
                :class="{ 'kb-nav-focus': ppIndex === 0 }"
                @click="closePanel"
              >{{ paused ? '继续游戏' : '返回游戏' }}</button>
              <button :ref="(el) => setPpBtn(el, 1)" class="pp-btn" :class="{ 'kb-nav-focus': ppIndex === 1 }" @click="startGame">
                重新开始
              </button>
              <button
                :ref="(el) => setPpBtn(el, 2)"
                class="pp-btn danger"
                :class="{ 'kb-nav-focus': ppIndex === 2 }"
                @click="settle"
              >退出游戏</button>
              <p class="pp-keys">↑↓ 选择 · 回车确认</p>
            </div>
          </div>
        </Transition>
      </div>

      <!-- 底部输入条：锁定单词后才显示，逐字符高亮 -->
      <div v-if="lockedWord" class="input-bar" :class="[inputFlash, { shake: inputShake }]">
        <div class="word-mirror">
          <template v-for="(ch, i) in lockedWord.spelling.split('')" :key="i">
            <span v-if="isBlockSlot(i)" class="mirror-ch block" title="空格错误"></span>
            <span v-else class="mirror-ch" :class="mirrorClass(i)">{{ mirrorChar(i, ch) }}</span>
          </template>
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
/* ---- 配置 + 排行榜：宽屏两栏，窄屏堆叠 ---- */
.idle-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: flex-start;
}
.idle-grid .cfg-card {
  flex: 0 1 640px;
  min-width: 320px;
}
.lb-card {
  flex: 1 1 380px;
  min-width: 320px;
}
.lb-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.lb-title {
  font-weight: 700;
  color: var(--wt-text);
}
.lb-scope {
  font-size: 12px;
  color: var(--wt-text-3);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lb-sub {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 10px 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--wt-text-2);
}
.lb-sub:first-of-type {
  margin-top: 0;
}
.lb-count {
  font-size: 12px;
  font-weight: 400;
  color: var(--wt-text-3);
}
.lb-rank {
  font-weight: 700;
  color: var(--wt-text-2);
}
.lb-name-me {
  font-weight: 700;
  color: var(--wt-primary);
}
.lb-name-me em {
  font-style: normal;
  font-size: 12px;
}
/* 自己所在行高亮（el-table row-class-name 在 scoped 下需 :deep） */
:deep(.el-table .lb-me td.el-table__cell) {
  background: var(--wt-primary-bg);
}
.hint {
  font-size: 13.5px;
  line-height: 2;
  color: #6b7280;
  margin-top: 12px;
}
.hint b {
  color: #374151;
  font-weight: 700;
}
.hint .hl-title {
  display: inline-block;
  padding: 0 8px;
  margin-right: 6px;
  border-radius: 4px;
  background: var(--wt-primary-weak, rgba(59, 130, 246, 0.12));
  color: var(--wt-primary, #3b82f6);
  font-size: 12.5px;
  font-weight: 700;
}
.hint .c-good {
  color: #16a34a;
}
.hint .c-near {
  color: #d97706;
}
.hint .c-bad {
  color: #dc2626;
}
/* ---- 对局区：全屏沉浸深色，85% 居中（扣掉上收但仍占位的顶栏高度，避免多出可滚动余量） ---- */
.game-page {
  min-height: calc(100vh - var(--wt-nav-h, 60px));
  min-height: calc(100dvh - var(--wt-nav-h, 60px));
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
/* ---- 血条（500 点，扣 (1-得分比)×100）---- */
.hud-hp {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 190px;
  padding: 2px 6px;
  border-radius: 8px;
  transition: background 0.2s;
}
.hud-hp.hurt {
  animation: hurt-blink 0.6s ease;
}
@keyframes hurt-blink {
  0%, 100% { background: transparent; }
  25%, 75% { background: rgba(255, 77, 79, 0.35); }
  50% { background: rgba(255, 77, 79, 0.55); }
}
.hp-track {
  position: relative;
  flex: 1;
  height: 12px;
  min-width: 110px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.22);
  overflow: hidden;
}
.hp-fill {
  height: 100%;
  border-radius: 6px;
  background: linear-gradient(90deg, #52c41a, #73d13d);
  box-shadow: 0 0 8px rgba(82, 196, 26, 0.5);
  transition: width 0.25s ease, background 0.25s ease;
}
.hud-hp.warn .hp-fill {
  background: linear-gradient(90deg, #fa8c16, #ffc069);
  box-shadow: 0 0 8px rgba(250, 140, 22, 0.55);
}
.hud-hp.danger .hp-fill {
  background: linear-gradient(90deg, #ff4d4f, #ff7875);
  box-shadow: 0 0 10px rgba(255, 77, 79, 0.6);
  animation: hp-danger 0.9s ease-in-out infinite;
}
@keyframes hp-danger {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
.hp-num {
  min-width: 74px;
  font-size: 15px;
  font-weight: 800;
  color: #fff;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.6);
}
.hp-num i {
  font-style: normal;
  font-size: 12px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.55);
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
/** 销毁结果浮层：全对=金 / 未全对=橙（显示得分比百分比），上浮淡出 */
.destroy-pop {
  position: absolute;
  z-index: 6;
  transform: translate(-50%, -50%);
  font: 700 22px 'JetBrains Mono', Consolas, monospace;
  pointer-events: none;
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.6);
  animation: pop-rise 0.95s ease-out forwards;
  white-space: nowrap;
}
.destroy-pop.good {
  color: #f5c542;
}
.destroy-pop.near {
  color: #ff9f43;
}
/* 漏词落地：红色「−N」 */
.destroy-pop.bad {
  color: #ff4d4f;
}
@keyframes pop-rise {
  0% {
    opacity: 0;
    margin-top: 0;
    scale: 0.7;
  }
  15% {
    opacity: 1;
    scale: 1.15;
  }
  70% {
    opacity: 1;
  }
  100% {
    opacity: 0;
    margin-top: -46px;
    scale: 1;
  }
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
/* ↑↓ 键盘选中态：面板内深色背景，用亮描边而非浅色 outline（.kb-nav-focus 的默认色在深底上看不清） */
.pp-btn.kb-nav-focus {
  outline: 2px solid #93c5fd;
  outline-offset: 2px;
  box-shadow: 0 0 0 4px rgba(147, 197, 253, 0.25);
}
.pp-keys {
  margin: 10px 0 0;
  font-size: 12px;
  color: #9ca3af;
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
/* 空格敲错：红色方块 */
.mirror-ch.block {
  display: inline-block;
  width: 1ch;
  height: 22px;
  background: #ff4d4f;
  border-radius: 3px;
  box-shadow: 0 0 8px rgba(255, 77, 79, 0.55);
  vertical-align: middle;
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
