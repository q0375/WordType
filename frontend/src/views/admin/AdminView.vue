<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { confirmAction } from '../../core/confirm';
import { adminApi } from '../../api';
import { useSettingsStore } from '../../stores/settings';

const settingsStore = useSettingsStore();

const tab = ref('users');
const users = ref<any[]>([]);
const usersTotal = ref(0);
const userQuery = ref('');
// 默认只看「正常」用户：日常管理（重置密码/调班级）对象几乎都是正常用户，已注销在专门筛选里看
const userStatus = ref('active');
const userPage = ref(1);
const USER_PAGE_SIZE = 20;
const codes = ref<any[]>([]);
const codesTotal = ref(0);
const codeStatus = ref('');
const codePage = ref(1);
const CODE_PAGE_SIZE = 20;

// ---- 班级（D34） ----
const classes = ref<any[]>([]);
const newClassName = ref('');

onMounted(() => {
  loadUsers();
  loadCodes();
  loadClasses();
  loadPapers();
  loadDevSummary();
  loadFallMult();
});

async function loadClasses() {
  const { data } = await adminApi.classes();
  classes.value = data.items;
}

async function addClass() {
  if (!newClassName.value.trim()) return;
  await adminApi.createClass(newClassName.value.trim());
  newClassName.value = '';
  ElMessage.success({ message: '班级已创建', duration: 2000, showClose: true });
  loadClasses();
}

async function renameClass(c: any) {
  const { value } = await ElMessageBox.prompt('新名称', '重命名班级', { inputValue: c.name });
  await adminApi.renameClass(c.id, value);
  loadClasses();
  loadUsers();
}

async function removeClass(c: any) {
  await ElMessageBox.confirm(`删除班级「${c.name}」？`, '删除班级', { type: 'warning' });
  await adminApi.deleteClass(c.id);
  ElMessage.success({ message: '已删除', duration: 2000, showClose: true });
  loadClasses();
}

async function changeUserClass(u: any, classId: number | null) {
  await adminApi.setUserClass(u.id, classId);
  ElMessage.success({ message: `已将 ${u.username} 调整班级`, duration: 2000, showClose: true });
  loadUsers();
  loadClasses();
}

// ---- 考核卷（D36）：新建界面与逻辑对齐个人端考核组卷（ExamView） ----
const papers = ref<any[]>([]);
const paperDialog = ref(false);
const paperSaving = ref(false);
const paperForm = ref({ name: '', bookId: null as number | null, chapterIds: [] as number[] }); // 章节可多选（2026-09-22）
const paperCfg = ref({ dictation: 0, choice: 0, cloze: 0, listening: 0 });
const paperTimeLimit = ref(60);
const paperPassScore = ref(60);
// 每词限时档位（与个人端一致）：宽松 ×1.5 / 标准 ×1.0 / 紧凑 ×0.7，只改每题倒计时，不影响计分
type PaperTimePolicy = 'relaxed' | 'standard' | 'tight';
const paperTimePolicy = ref<PaperTimePolicy>('standard');
const paperChapters = ref<any[]>([]);
const adminBooks = ref<any[]>([]); // 仅公共词库：全班同卷，私有库学生不可见
const assignDialog = ref(false);
const assignTarget = ref<any>(null);
const assignForm = ref({ classId: null as number | null, available_at: '', expires_at: '', max_attempts: null as number | null });

// 题型行（与 ExamView 一致：竖排 + 每行 max 按钮）
type PaperQType = 'dictation' | 'choice' | 'cloze' | 'listening';
const paperTypeRows: ReadonlyArray<{ key: PaperQType; label: string }> = [
  { key: 'dictation', label: '默写' },
  { key: 'choice', label: '选择' },
  { key: 'cloze', label: '挖空' },
  { key: 'listening', label: '听音' },
];

// 多章节复选（2026-09-22）：题量上限 = 已选章节合计词数
const paperChapterWordCount = computed(() => paperChapters.value.filter((c) => paperForm.value.chapterIds.includes(c.id)).reduce((a, c) => a + (c.word_count || 0), 0));
const paperTypeMax = computed(() => (paperChapterWordCount.value > 0 ? paperChapterWordCount.value : 0));
const paperTotal = computed(() => Object.values(paperCfg.value).reduce((a, b) => a + b, 0));
const paperCountOver = computed(() => paperChapterWordCount.value > 0 && paperTotal.value > paperChapterWordCount.value);
const paperNoWords = computed(() => paperForm.value.chapterIds.length > 0 && paperChapterWordCount.value === 0);

// 全选/反选（2026-09-22）：下拉头部"全选"复选框，勾上选全部章节，支持半选态
const paperAllChaptersSelected = computed(() => paperChapters.value.length > 0 && paperForm.value.chapterIds.length === paperChapters.value.length);
const paperSomeChaptersSelected = computed(() => paperForm.value.chapterIds.length > 0 && !paperAllChaptersSelected.value);
function togglePaperAllChapters(v: string | number | boolean) {
  paperForm.value.chapterIds = v ? paperChapters.value.map((c) => c.id) : [];
}

// 已选章节合计词数变化后按合计预填一套合法题量（与 ExamView 同规则）
watch(
  () => [paperForm.value.chapterIds, paperChapters.value] as const,
  ([ids, chs]) => {
    const n = chs.filter((c: any) => (ids as number[]).includes(c.id)).reduce((a: number, c: any) => a + (c.word_count || 0), 0);
    if (n <= 0) {
      paperCfg.value = { dictation: 0, choice: 0, cloze: 0, listening: 0 };
      return;
    }
    const d = Math.min(5, n);
    const ch = n - d >= 3 ? 3 : Math.max(0, n - d);
    paperCfg.value = { dictation: d, choice: ch, cloze: 0, listening: 0 };
  },
);

function maxOnlyPaperType(k: PaperQType) {
  const n = paperChapterWordCount.value;
  if (!n) {
    ElMessage.warning({ message: '请先选择章节，max 按所选章节合计词数计算', duration: 2000, showClose: true });
    return;
  }
  const next: Record<PaperQType, number> = { dictation: 0, choice: 0, cloze: 0, listening: 0 };
  next[k] = n;
  paperCfg.value = next;
}

async function loadPapers() {
  const { data } = await adminApi.examPapers();
  papers.value = data.items;
}

async function openPaperDialog() {
  // 每次打开都重置表单并刷新公共词库列表（新建的公共库立即可选）
  paperForm.value = { name: '', bookId: null, chapterIds: [] };
  paperCfg.value = { dictation: 0, choice: 0, cloze: 0, listening: 0 };
  paperTimeLimit.value = 60;
  paperPassScore.value = 60;
  paperTimePolicy.value = 'standard';
  paperChapters.value = [];
  paperDialog.value = true;
  const { data: bd } = await (await import('../../api')).booksApi.list('public', '', 1, 500);
  adminBooks.value = bd.items;
}

async function onPaperBookChange(id: number) {
  paperForm.value.chapterIds = [];
  paperCfg.value = { dictation: 0, choice: 0, cloze: 0, listening: 0 };
  const { data } = await (await import('../../api')).booksApi.chapters(id);
  paperChapters.value = data.items;
}

async function createPaper() {
  const f = paperForm.value;
  if (!f.name.trim() || !f.chapterIds.length) {
    ElMessage.warning({ message: '请填写考核卷名称并选择章节（可多选）', duration: 2000, showClose: true });
    return;
  }
  if (paperNoWords.value) {
    ElMessage.warning({ message: '所选章节没有单词，请换章节', duration: 2000, showClose: true });
    return;
  }
  if (paperTotal.value <= 0) {
    ElMessage.warning({ message: '题量必须大于 0', duration: 2000, showClose: true });
    return;
  }
  if (paperCountOver.value) {
    ElMessage.warning({
      message: `题目总数 ${paperTotal.value} 超过所选章节合计词数 ${paperChapterWordCount.value}，请减少题量`,
      duration: 2000,
      showClose: true,
    });
    return;
  }
  const type_counts: Record<string, number> = {};
  for (const [k, v] of Object.entries(paperCfg.value)) if (v > 0) type_counts[k] = v;
  paperSaving.value = true;
  try {
    const { data } = await adminApi.createExamPaper({
      name: f.name.trim(),
      chapter_ids: f.chapterIds,
      type_counts,
      time_limit_min: paperTimeLimit.value,
      pass_score: paperPassScore.value,
      time_policy: paperTimePolicy.value,
    });
    ElMessage.success({ message: `考核卷已生成（固定 ${data.question_count} 题，所有参与者同卷）`, duration: 2000, showClose: true });
    paperDialog.value = false;
    loadPapers();
  } finally {
    paperSaving.value = false;
  }
}

function openAssign(t: any) {
  assignTarget.value = t;
  assignForm.value = { classId: null, available_at: '', expires_at: '', max_attempts: null };
  assignDialog.value = true;
}

async function doAssign() {
  const f = assignForm.value;
  if (!f.classId) {
    ElMessage.warning({ message: '请选择班级', duration: 2000, showClose: true });
    return;
  }
  await adminApi.assignPaper(assignTarget.value.id, {
    class_id: f.classId,
    available_at: f.available_at || null,
    expires_at: f.expires_at || null,
    max_attempts: f.max_attempts || null,
  });
  ElMessage.success({ message: f.max_attempts ? `已分配（每人限考 ${f.max_attempts} 次）` : '已分配（到设定时间自动启用）', duration: 2000, showClose: true });
  assignDialog.value = false;
  loadPapers();
}

async function removeAssign(a: any) {
  try {
    await ElMessageBox.confirm(
      `确定取消班级「${a.class_name}」的这份考核吗？仅移除分配关系，学生已产生的考试成绩不会被删除。`,
      '取消分配',
      { type: 'warning', confirmButtonText: '取消分配', cancelButtonText: '保留' },
    );
  } catch {
    return;
  }
  await adminApi.deleteAssignment(a.id);
  loadPapers();
}

async function removePaper(t: any) {
  await ElMessageBox.confirm(`删除考核卷「${t.name}」及其全部分配关系？`, '删除考核卷', { type: 'warning' });
  await adminApi.deleteExamPaper(t.id);
  loadPapers();
}

// ---- 查看考核卷题目（2026-09-23）：开卷前预览全班同卷内容 ----
const tqDrawer = ref(false);
const tqLoading = ref(false);
const tqName = ref('');
const tqItems = ref<any[]>([]);
const TQ_TYPE_LABEL: Record<string, string> = { dictation: '听写', choice: '选择', cloze: '填空', listening: '听力' };

async function openTemplateQuestions(t: any) {
  tqName.value = `${t.name}（${t.question_count} 题）`;
  tqItems.value = [];
  tqDrawer.value = true;
  tqLoading.value = true;
  try {
    const { data } = await adminApi.examTemplateQuestions(t.id);
    tqItems.value = data.items ?? [];
  } finally {
    tqLoading.value = false;
  }
}

function tqPrompt(q: any): string {
  const p = q.payload || {};
  if (q.type === 'dictation') return p.meaning || '—';
  if (q.type === 'listening') return `🔊 播放发音：${p.spelling}`;
  if (q.type === 'cloze') return p.sentence_masked || '—';
  if (q.type === 'choice') return `${p.phonetic || '—'}（四选一，正确选项见「答案」列）`;
  return '—';
}

function tqAnswer(q: any): string {
  return q.type === 'choice' ? q.meaning || '—' : q.spelling || '—';
}

async function togglePaper(t: any) {
  // 停用会立刻让所有已分配班级无法开考（影响面大且不易察觉），停用需确认；启用无风险直接执行
  // 审计补漏 2026-10-03
  if (t.is_active) {
    const ok = await confirmAction({
      title: '停用考核卷',
      message: `停用后「${t.name}」将无法被学生开考（已产生的成绩保留），可随时重新启用。确定停用？`,
      confirmText: '停用',
      cancelText: '取消',
    });
    if (!ok) return;
  }
  await adminApi.patchExamPaper(t.id, { is_active: !t.is_active });
  loadPapers();
}

const isAssignedClass = (classId: number) => !!assignTarget.value?.assignments?.some((a: any) => a.class_id === classId);

async function removeAssignInDialog(a: any) {
  try {
    await ElMessageBox.confirm(
      `确定取消班级「${a.class_name}」的这份考核吗？仅移除分配关系，学生已产生的考试成绩不会被删除。`,
      '取消分配',
      { type: 'warning', confirmButtonText: '取消分配', cancelButtonText: '保留' },
    );
  } catch {
    return;
  }
  await adminApi.deleteAssignment(a.id);
  await loadPapers();
  // 对话框内移除后，同步刷新目标卷的分配列表
  assignTarget.value = papers.value.find((p) => p.id === assignTarget.value?.id) ?? assignTarget.value;
}

function assignState(a: any): { label: string; type: 'success' | 'warning' | 'info' } {
  const now = Date.now();
  const av = a.available_at ? new Date(a.available_at.replace(' ', 'T')).getTime() : 0;
  const ex = a.expires_at ? new Date(a.expires_at.replace(' ', 'T')).getTime() : Infinity;
  if (now < av) return { label: '未生效', type: 'warning' };
  if (now > ex) return { label: '已截止', type: 'info' };
  return { label: '生效中', type: 'success' };
}

// ==================== 考核口径（只读说明，无任何可配置项） ====================
// 口径唯一真相源：后端 domain/defaults.py；游戏侧对应前端 core/gameRules.ts。
// - 得分比 / 分级容差 2026-09-19 取消：每题得分 = 得分比(输入正确字符数/全部字符数) × 该题原始时限；
// - 每词限时档位与倍数 2026-09-20 取消：每题倒计时 = 原始时限向上取整，与计分权重同源。
const examCfgDialog = ref(false);

function openExamCfg() {
  examCfgDialog.value = true;
}

// ==================== 系统设置（全局唯一可配口径项） ====================
// 下坠倍数：单词出现 → 消失 = 该词时限 × mult，默认 2；仅影响动画节奏，计分（得分比 × 时限）不变。
const fallMult = ref(2);
const fallSaving = ref(false);

async function loadFallMult() {
  try {
    await settingsStore.load();
  } catch {
    return; /* 读取失败保持默认展示值，保存时以后端校验为准 */
  }
  const cur = Number(settingsStore.settings?.game_fall_duration_mult);
  if (Number.isFinite(cur) && cur > 0) fallMult.value = cur;
}

async function saveFallMult() {
  fallSaving.value = true;
  try {
    const { data } = await adminApi.setGameFallMult(fallMult.value);
    if (settingsStore.settings) settingsStore.settings.game_fall_duration_mult = data.game_fall_duration_mult;
    ElMessage.success({ message: '下坠倍数已保存，立即生效', duration: 2000, showClose: true });
  } catch {
    /* 错误由拦截器统一提示 */
  } finally {
    fallSaving.value = false;
  }
}

// ==================== 考核卷成绩（查看 / 导出） ====================
const resultDialog = ref(false);
const resultTab = ref('students');
const resultLoading = ref(false);
const resultData = ref<any>(null);
const questionStats = ref<any>(null);
const studentDetail = ref<any>(null);
const detailDrawer = ref(false);
const detailLoading = ref(false);

async function openResults(t: any) {
  resultDialog.value = true;
  resultTab.value = 'students';
  studentDetail.value = null;
  resultLoading.value = true;
  try {
    const [{ data: res }, { data: qs }] = await Promise.all([
      adminApi.paperResults(t.id),
      adminApi.paperQuestionStats(t.id),
    ]);
    resultData.value = res;
    questionStats.value = qs;
  } catch {
    resultData.value = null;
    questionStats.value = null;
  } finally {
    resultLoading.value = false;
  }
}

async function showStudentDetail(row: any) {
  const rid = row.best_record_id ?? row.last_record_id;
  if (!rid) {
    ElMessage.info({ message: '该学生尚未交卷', duration: 2000, showClose: true });
    return;
  }
  detailLoading.value = true;
  studentDetail.value = null;
  try {
    const { data } = await adminApi.adminExamRecord(rid);
    studentDetail.value = data;
    detailDrawer.value = true;
  } finally {
    detailLoading.value = false;
  }
}

// 题目分析：单题作答明细
const qDrawer = ref(false);
const qDetailLoading = ref(false);
const qDetail = ref<any>(null);

async function openQuestionAnswers(row: any) {
  qDrawer.value = true;
  qDetailLoading.value = true;
  qDetail.value = null;
  try {
    const { data } = await adminApi.paperQuestionAnswers(resultData.value.template.id, row.qid);
    qDetail.value = data;
  } finally {
    qDetailLoading.value = false;
  }
}

async function downloadResultsCsv(t: any) {
  try {
    const { data } = await adminApi.paperResultsCsv(t.id);
    const blob = data instanceof Blob ? data : new Blob([data], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `exam-results-${t.id}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch {
    ElMessage.error({ message: '导出失败', duration: 2000, showClose: true });
  }
}

const resultLabel = (r: string) => ({ correct: '正确', near: '近似', wrong: '错误' })[r] ?? r;
const rateClass = (v: number | null) => (v === null ? '' : v >= 80 ? 'rate-high' : v >= 50 ? 'rate-mid' : 'rate-low');

const fmtLocal = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};

// 在线判定（2026-09-22）：last_seen_at 在 5 分钟内视为在线（后端认证请求节流刷新）
const ONLINE_WINDOW_MS = 5 * 60 * 1000;
function isOnline(u: any): boolean {
  if (u.is_deleted || !u.last_seen_at) return false;
  const t = new Date(String(u.last_seen_at).replace(' ', 'T')).getTime();
  return Date.now() - t <= ONLINE_WINDOW_MS;
}

async function loadUsers() {
  const { data } = await adminApi.users({ q: userQuery.value || undefined, status: userStatus.value, page: userPage.value });
  users.value = data.items;
  usersTotal.value = data.total;
}

function onUserPageChange(p: number) {
  userPage.value = p;
  loadUsers();
}

// 重置密码（2026-09-21 重做）：对话框选择「随机生成 / 指定密码」，确认后才真正生效；
// 已注销用户不提供入口（服务端同样拒绝）
const resetDialog = ref(false);
const resetTarget = ref<any>(null);
const resetSaving = ref(false);
const resetMode = ref<'random' | 'custom'>('random');
const resetPwd = ref('');

function openReset(u: any) {
  resetTarget.value = u;
  resetMode.value = 'random';
  resetPwd.value = '';
  resetDialog.value = true;
}

async function doReset() {
  if (resetMode.value === 'custom') {
    const p = resetPwd.value;
    if (p.length < 8 || !/[a-zA-Z]/.test(p) || !/\d/.test(p)) {
      ElMessage.warning({ message: '密码需至少 8 位且同时包含字母与数字', duration: 2000, showClose: true });
      return;
    }
  }
  resetSaving.value = true;
  try {
    const { data } = await adminApi.resetPassword(resetTarget.value.id, resetMode.value === 'custom' ? resetPwd.value : undefined);
    await ElMessageBox.alert(
      resetMode.value === 'random'
        ? `临时密码（仅本次可见，请转告用户并提示首次登录改密）：\n${data.temp_password}`
        : '密码已重置为指定密码，该用户全部旧登录会话已失效',
      '重置成功',
    );
    resetDialog.value = false;
  } catch {
    /* 校验错误由拦截器提示 */
  } finally {
    resetSaving.value = false;
  }
}

// 批量导入用户
const importUsersDialog = ref(false);
const importUsersText = ref('');
const importUsersResult = ref<{ created: any[]; skipped: any[] } | null>(null);

function openImportUsers() {
  importUsersText.value = '';
  importUsersResult.value = null;
  importUsersDialog.value = true;
}

function parseImportUsers(): { username: string; password?: string; class_name?: string }[] {
  return importUsersText.value
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const [username, password, className] = line.split(/[,，;；\t]/).map((s) => s.trim());
      return { username, password: password || undefined, class_name: className || undefined };
    })
    .filter((u) => u.username);
}

async function doImportUsers() {
  const rows = parseImportUsers();
  if (!rows.length) {
    ElMessage.warning({ message: '请先粘贴用户数据（每行：用户名,密码,班级）', duration: 2000, showClose: true });
    return;
  }
  try {
    const { data } = await adminApi.importUsers(rows);
    importUsersResult.value = data;
    if (data.created_count > 0) {
      await loadClasses(); // 可能自动创建了新班级，先刷新班级列表再刷新用户，保证班级列显示正确
      loadUsers();
    }
  } catch {
    /* 校验错误由拦截器提示 */
  }
}

async function restoreUser(u: any) {
  try {
    await adminApi.restore(u.id);
    ElMessage.success({ message: '已恢复', duration: 2000, showClose: true });
    loadUsers();
  } catch {
    /* RESTORE_WINDOW_EXPIRED 由拦截器提示 */
  }
}

async function loadCodes() {
  const { data } = await adminApi.inviteCodes({ status: codeStatus.value || undefined, page: codePage.value });
  codes.value = data.items;
  codesTotal.value = data.total;
}

async function genCodes() {
  const { value } = await ElMessageBox.prompt('生成数量（1–100）与有效天数，格式：数量,天数', '批量生成邀请码', { inputValue: '10,30' });
  const [count, days] = value.split(',').map((s: string) => parseInt(s.trim(), 10));
  const { data } = await adminApi.createInviteCodes(count, days);
  await ElMessageBox.alert(data.codes.join('\n'), '已生成（一次性使用）');
  loadCodes();
}

async function toggleCode(c: any) {
  await adminApi.patchInviteCode(c.id, c.is_active ? 0 : 1);
  loadCodes();
}

// ---- 测试数据调整（dev-data） ----
const devUser = ref<number | null>(null); // null = 管理员自己
const devSummary = ref<any>(null);
const devSeeding = ref(false);
const dev = ref({
  high_error_n: 10,
  danger_due_n: 5,
  inactive_days: 0,
  weak_bigram_n: 5,
  typing_count: 30,
  typing_wpm: 40,
  typing_accuracy: 0.9,
  game_count: 10,
});

async function loadDevSummary() {
  const { data } = await adminApi.devdataSummary(devUser.value ?? undefined);
  devSummary.value = data;
}

async function seedDevData() {
  devSeeding.value = true;
  try {
    const p: any = { target_user_id: devUser.value ?? undefined };
    for (const [k, v] of Object.entries(dev.value)) {
      if (v && v > 0) p[k] = v;
    }
    const { data } = await adminApi.devdataSeed(p);
    devSummary.value = data.summary;
    await ElMessageBox.alert(data.done.join('\n') || '未选择任何注入项', '注入完成');
  } finally {
    devSeeding.value = false;
  }
}

async function clearDevData() {
  await ElMessageBox.confirm('将删除该用户的全部学习数据（词统计/错题/打字/游戏/考核/复习队列等），不可恢复。确认？', '清空学习数据', {
    type: 'warning',
    confirmButtonText: '全部清空',
    confirmButtonClass: 'el-button--danger',
  });
  const { data } = await adminApi.devdataClear(devUser.value ?? undefined);
  devSummary.value = data.summary;
  ElMessage.success({ message: `已清除 ${data.cleared_rows} 行数据`, duration: 2000, showClose: true });
}

watch(devUser, loadDevSummary);
</script>

<template>
  <PageShell title="管理后台" subtitle="用户 · 邀请码 · 测试数据（admin）">
    <el-tabs v-model="tab">
      <!-- 用户管理 -->
      <el-tab-pane label="用户管理" name="users">
        <div class="toolbar">
          <el-input v-model="userQuery" placeholder="搜索用户名" style="width: 200px" clearable @keyup.enter="loadUsers" />
          <el-radio-group v-model="userStatus" @change="userPage = 1; loadUsers()">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="active">正常</el-radio-button>
            <el-radio-button value="deleted">已注销</el-radio-button>
          </el-radio-group>
          <el-button @click="userPage = 1; loadUsers()">查询</el-button>
          <div class="spacer"></div>
          <el-button type="primary" @click="openImportUsers">批量导入用户</el-button>
        </div>
        <el-table :data="users" size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" label="用户名" />
          <el-table-column label="昵称" width="130">
            <template #default="{ row }">{{ row.nickname || '—' }}</template>
          </el-table-column>
          <el-table-column label="在线" width="80">
            <template #default="{ row }">
              <el-tag v-if="isOnline(row)" type="success" size="small">在线</el-tag>
              <span v-else style="color: var(--wt-text-3); font-size: 12px">离线</span>
            </template>
          </el-table-column>
          <el-table-column prop="role" label="角色" width="90" />
          <el-table-column label="班级" width="170">
            <template #default="{ row }">
              <el-select :model-value="row.class_id ?? classes.find((c) => c.is_default)?.id" size="small" @change="(v: number) => changeUserClass(row, v)">
                <el-option v-for="c in classes" :key="c.id" :label="c.name + (c.is_default ? '（默认）' : '')" :value="c.id" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="140">
            <template #default="{ row }">
              <el-tag v-if="!row.is_deleted" type="success" size="small">正常</el-tag>
              <el-tag v-else type="danger" size="small">已注销{{ row.deleted_at ? `（${row.deleted_at.slice(0, 10)}）` : '' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="最近活跃" width="170">
            <template #default="{ row }">
              <span :title="row.last_login_at ? `最近登录：${row.last_login_at}` : ''">{{ row.last_seen_at || row.last_login_at || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <!-- 已注销用户不提供重置密码入口（服务端同步拒绝） -->
              <el-button v-if="!row.is_deleted" size="small" type="warning" plain @click="openReset(row)">重置密码</el-button>
              <el-button v-if="row.is_deleted" size="small" type="success" plain @click="restoreUser(row)">恢复</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div style="margin-top: 12px; display: flex; justify-content: flex-end">
          <el-pagination
            v-model:current-page="userPage"
            :page-size="USER_PAGE_SIZE"
            :total="usersTotal"
            layout="total, prev, pager, next"
            background
            hide-on-single-page
            @current-change="onUserPageChange"
          />
        </div>
      </el-tab-pane>

      <!-- 邀请码 -->
      <el-tab-pane label="邀请码管理" name="codes">
        <div class="toolbar">
          <el-button type="primary" @click="genCodes">批量生成</el-button>
          <el-select v-model="codeStatus" placeholder="状态筛选" clearable style="width: 140px" @change="codePage = 1; loadCodes()">
            <el-option label="可用" value="active" />
            <el-option label="已使用" value="used" />
            <el-option label="已停用" value="disabled" />
          </el-select>
          <div class="spacer"></div>
          <el-button @click="codePage = 1; loadCodes()">刷新</el-button>
        </div>
        <el-table :data="codes" size="small">
          <el-table-column prop="code" label="邀请码" width="160" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag v-if="row.used_by" type="info" size="small">已使用</el-tag>
              <el-tag v-else-if="row.is_active" type="success" size="small">可用</el-tag>
              <el-tag v-else type="danger" size="small">已停用</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="used_at" label="使用时间" width="170" />
          <el-table-column prop="expires_at" label="过期时间" width="170" />
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button v-if="!row.used_by" size="small" :type="row.is_active ? 'danger' : 'success'" plain @click="toggleCode(row)">
                {{ row.is_active ? '停用' : '启用' }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <div style="margin-top: 12px; display: flex; justify-content: flex-end">
          <el-pagination
            v-model:current-page="codePage"
            :page-size="CODE_PAGE_SIZE"
            :total="codesTotal"
            layout="total, prev, pager, next"
            background
            hide-on-single-page
            @current-change="loadCodes"
          />
        </div>
      </el-tab-pane>

      <!-- 班级管理 -->
      <el-tab-pane label="班级管理" name="classes">
        <div class="toolbar">
          <el-input v-model="newClassName" placeholder="新班级名称" style="width: 200px" @keyup.enter="addClass" />
          <el-button type="primary" @click="addClass">新建班级</el-button>
        </div>
        <el-table :data="classes" size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column label="班级名称">
            <template #default="{ row }">
              {{ row.name }}
              <el-tag v-if="row.is_default" type="info" size="small">默认班级（未分配用户自动归属）</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="member_count" label="成员数" width="90" />
          <el-table-column label="操作" width="170">
            <template #default="{ row }">
              <el-button v-if="!row.is_default" size="small" @click="renameClass(row)">重命名</el-button>
              <el-button v-if="!row.is_default" size="small" type="danger" plain @click="removeClass(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 考核卷 -->
      <el-tab-pane label="考核卷" name="papers">
        <div class="toolbar">
          <el-button type="primary" @click="openPaperDialog">新建考核卷</el-button>
          <el-button @click="openExamCfg">考核口径说明</el-button>
          <span class="hint">同一张考核卷对每位参与者题目完全一致；分配给班级后按设定时间自动启用</span>
        </div>
        <el-table :data="papers" size="small">
          <el-table-column prop="id" label="ID" width="55" />
          <el-table-column prop="name" label="名称" min-width="140" />
          <el-table-column label="章节" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">
              {{ (row.chapter_names && row.chapter_names.join('、')) || row.chapter_name }}
            </template>
          </el-table-column>
          <el-table-column label="题量" width="70">
            <template #default="{ row }">{{ row.question_count }}</template>
          </el-table-column>
          <el-table-column label="限时" width="110">
            <template #default="{ row }">整卷 {{ row.time_limit_min }}min</template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="分配班级" min-width="200">
            <template #default="{ row }">
              <template v-if="row.assignments.length">
                <el-tag
                  v-for="a in row.assignments"
                  :key="a.id"
                  :type="assignState(a).type"
                  size="small"
                  closable
                  style="margin: 2px 4px 2px 0"
                  @close="removeAssign(a)"
                >
                  {{ a.class_name || `#${a.class_id}` }} · {{ assignState(a).label }}
                  <template v-if="a.available_at">· {{ a.available_at.slice(5, 16) }}</template>
                  <template v-if="a.max_attempts">· 限{{ a.max_attempts }}次</template>
                </el-tag>
              </template>
              <span v-else class="hint">未分配</span>
            </template>
          </el-table-column>
          <!-- 5 个按钮：内容 254 + 间隔 48 + cell padding 24 ≈ 326px，320 会把「删除」挤换行 -->
          <el-table-column label="操作" width="352">
            <template #default="{ row }">
              <el-button size="small" type="success" plain @click="openResults(row)">成绩</el-button>
              <el-button size="small" type="primary" plain @click="openAssign(row)">分配班级</el-button>
              <el-button size="small" plain @click="openTemplateQuestions(row)">题目</el-button>
              <el-button size="small" @click="togglePaper(row)">{{ row.is_active ? '停用' : '启用' }}</el-button>
              <el-button size="small" type="danger" plain @click="removePaper(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 测试数据调整 -->
      <el-tab-pane label="测试数据" name="devdata">
        <el-card shadow="never" class="ai-card">
          <el-form label-width="150px">
            <el-form-item label="目标用户">
              <el-select v-model="devUser" placeholder="我自己（admin）" clearable filterable style="width: 300px">
                <el-option v-for="u in users" :key="u.id" :label="`#${u.id} ${u.username}（${u.role}）`" :value="u.id" />
              </el-select>
            </el-form-item>
          </el-form>

          <el-descriptions v-if="devSummary" :column="3" size="small" border style="margin-bottom: 8px">
            <el-descriptions-item label="词统计条数">{{ devSummary.word_stat_total }}</el-descriptions-item>
            <el-descriptions-item label="高错误率词">{{ devSummary.high_error }}</el-descriptions-item>
            <el-descriptions-item label="高危明日到期">{{ devSummary.danger_due_tomorrow }}</el-descriptions-item>
            <el-descriptions-item label="活跃天数">{{ devSummary.activity_days }}</el-descriptions-item>
            <el-descriptions-item label="打字记录">{{ devSummary.typing_records }}</el-descriptions-item>
            <el-descriptions-item label="游戏记录">{{ devSummary.game_records }}</el-descriptions-item>
            <el-descriptions-item label="错题（未攻克）">{{ devSummary.wrong_book }}</el-descriptions-item>
            <el-descriptions-item label="考核记录">{{ devSummary.exam_records }}</el-descriptions-item>
            <el-descriptions-item label="指法统计">{{ devSummary.letter_stats }}</el-descriptions-item>
          </el-descriptions>

          <el-divider content-position="left">注入测试数据（0 = 跳过该项）</el-divider>
          <el-form label-width="150px">
            <el-form-item label="高错误率词数">
              <el-input-number v-model="dev.high_error_n" :min="0" :max="200" />
              <span class="field-hint">错误率 &gt;60%，触发「专项练习」建议并入错题本</span>
            </el-form-item>
            <el-form-item label="高危明日到期词数">
              <el-input-number v-model="dev.danger_due_n" :min="0" :max="200" />
              <span class="field-hint">触发「复习」建议并写入复习队列</span>
            </el-form-item>
            <el-form-item label="清除近 N 天活跃">
              <el-input-number v-model="dev.inactive_days" :min="0" :max="30" />
              <span class="field-hint">≥7 天触发「连续未学」建议</span>
            </el-form-item>
            <el-form-item label="弱指法组合数">
              <el-input-number v-model="dev.weak_bigram_n" :min="0" :max="200" />
              <span class="field-hint">触发「指法专项」建议</span>
            </el-form-item>
            <el-form-item label="打字记录条数">
              <el-input-number v-model="dev.typing_count" :min="0" :max="200" />
              <span class="field-hint">随机分布在近 7 天，驱动趋势图</span>
            </el-form-item>
            <el-form-item label="打字 WPM / 正确率">
              <el-input-number v-model="dev.typing_wpm" :min="5" :max="300" />
              <el-input-number v-model="dev.typing_accuracy" :min="0.1" :max="1" :step="0.01" style="margin-left: 8px" />
            </el-form-item>
            <el-form-item label="游戏记录条数">
              <el-input-number v-model="dev.game_count" :min="0" :max="200" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="devSeeding" @click="seedDevData">注入数据</el-button>
              <el-button @click="loadDevSummary">刷新统计</el-button>
              <el-button type="danger" plain @click="clearDevData">清空该用户全部学习数据</el-button>
            </el-form-item>
          </el-form>
          <p class="hint">注入/清空后当日 AI 建议缓存自动失效，仪表盘与建议立即按新数据重算。仅测试用途。</p>
        </el-card>
      </el-tab-pane>

      <!-- 游戏：管理员全局游戏配置项 -->
      <el-tab-pane label="游戏" name="system">
        <el-card shadow="never" class="ai-card">
          <el-form label-width="150px">
            <el-form-item label="游戏下坠倍数">
              <div>
                <el-input-number v-model="fallMult" :min="1" :max="10" :step="0.5" :precision="1" controls-position="right" />
                <span style="margin-left: 8px">倍（默认 2，范围 1 ~ 10）</span>
                <div class="field-hint" style="margin-top: 4px">
                  游戏局面常有多词同屏：单词「出现 → 消失」= 该词时限 × 下坠倍数，倍数越大下坠越慢、节奏越从容；<br />
                  仅影响动画节奏，<b>计分不受影响</b>（得分仍 = 得分比 × 该词时限 × Combo），保存后立即对全部玩家生效
                </div>
              </div>
            </el-form-item>
          </el-form>
          <el-button type="primary" :loading="fallSaving" @click="saveFallMult">保存</el-button>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 重置密码：随机 / 指定密码，确认后生效（2026-09-21） -->
    <el-dialog v-model="resetDialog" :title="`重置密码 · ${resetTarget?.username ?? ''}`" width="460px">
      <el-form label-width="90px">
        <el-form-item label="重置方式">
          <el-radio-group v-model="resetMode">
            <el-radio-button value="random">随机生成</el-radio-button>
            <el-radio-button value="custom">指定密码</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="resetMode === 'custom'" label="新密码">
          <el-input v-model="resetPwd" type="password" show-password placeholder="至少 8 位，同时包含字母与数字" />
        </el-form-item>
        <p class="field-hint" style="margin-left: 0">
          确认后立即生效：该用户全部旧登录会话将失效。随机方式只在弹窗里显示一次临时密码，请及时转告用户。
        </p>
      </el-form>
      <template #footer>
        <el-button @click="resetDialog = false">取消</el-button>
        <el-button type="primary" :loading="resetSaving" @click="doReset">确认重置</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入用户 -->
    <el-dialog v-model="importUsersDialog" title="批量导入用户" width="640px">
      <el-alert type="info" :closable="false" style="margin-bottom: 12px">
        <template #title>每行一个用户：<b>用户名, 密码, 班级</b>（密码可留空自动生成；班级可省略，默认归默认班级；班级不存在会自动创建）</template>
        <div style="margin-top: 6px">
          <div>stu001, Abc12345, 一班</div>
          <div>stu002,, 一班（密码自动生成）</div>
          <div>stu003, Xyz98765（不写班级）</div>
        </div>
      </el-alert>
      <el-input v-model="importUsersText" type="textarea" :rows="8" placeholder="每行一个用户：用户名,密码,班级" />
      <template v-if="importUsersResult">
        <el-divider content-position="left">导入结果</el-divider>
        <p v-if="importUsersResult.created.length" class="hint" style="white-space: pre-wrap">
{{ importUsersResult.created.map((u: any) => `${u.username}  密码：${u.password}${u.auto_password ? '（自动生成）' : ''}${u.class_name ? '  班级：' + u.class_name : '  班级：默认班级'}`).join('\n') }}
        </p>
        <el-alert v-if="importUsersResult.created.some((u: any) => u.auto_password)" type="warning" :closable="false" title="自动生成的密码仅本次显示，请复制保存后分发给学生" style="margin-top: 8px" />
        <el-alert v-if="importUsersResult.skipped.length" type="error" :closable="false" style="margin-top: 8px">
          <template #title>跳过 {{ importUsersResult.skipped.length }} 行</template>
          <div v-for="s in importUsersResult.skipped" :key="s.line" style="font-size: 12px">第 {{ s.line }} 行 {{ s.username }}：{{ s.reason }}</div>
        </el-alert>
      </template>
      <template #footer>
        <el-button @click="importUsersDialog = false">关闭</el-button>
        <el-button type="primary" @click="doImportUsers">导入</el-button>
      </template>
    </el-dialog>

    <!-- 新建考核卷（界面与逻辑对齐个人端考核组卷） -->
    <el-dialog v-model="paperDialog" title="新建考核卷（创建后题目固定，全班同卷）" width="640px">
      <el-form label-width="110px">
        <el-form-item label="考核卷名称">
          <el-input v-model="paperForm.name" placeholder="如：Unit 1-3 月度考核" style="width: 320px" />
        </el-form-item>
        <el-form-item label="词库">
          <el-select v-model="paperForm.bookId" style="width: 320px" placeholder="选择公共词库" @change="onPaperBookChange">
            <el-option v-for="b in adminBooks" :key="b.id" :label="b.name" :value="b.id" />
          </el-select>
          <span class="hint">仅公共词库（全班同卷，私有词库对学生不可见）</span>
        </el-form-item>
        <el-form-item label="章节">
          <el-select v-model="paperForm.chapterIds" multiple collapse-tags collapse-tags-tooltip style="width: 320px" placeholder="选择章节（可多选）">
            <template #header>
              <el-checkbox :model-value="paperAllChaptersSelected" :indeterminate="paperSomeChaptersSelected" @change="togglePaperAllChapters">全选</el-checkbox>
            </template>
            <el-option v-for="c in paperChapters" :key="c.id" :label="`${c.name}（${c.word_count} 词）`" :value="c.id" />
          </el-select>
          <span v-if="paperChapterWordCount" class="hint">已选章节合计 {{ paperChapterWordCount }} 词，选题总数不能超过它</span>
        </el-form-item>
        <el-form-item label="题型 × 题量">
          <div class="tc-col">
            <div v-for="t in paperTypeRows" :key="t.key" class="tc-line">
              <el-input-number v-model="paperCfg[t.key]" :min="0" :max="paperTypeMax" />
              <span class="tc-label">{{ t.label }}</span>
              <el-button size="small" @click="maxOnlyPaperType(t.key)">max</el-button>
            </div>
          </div>
          <p v-if="!paperForm.chapterIds.length" class="field-hint--block">请先选择章节（可多选），题量上限按所选章节合计词数自动计算</p>
          <p v-else-if="paperNoWords" class="field-hint--block field-hint--warn">所选章节没有单词，无法组卷</p>
          <p v-else-if="paperCountOver" class="field-hint--block field-hint--warn">
            题目总数 {{ paperTotal }} 已超过所选章节合计词数 {{ paperChapterWordCount }}
          </p>
          <p v-else class="field-hint--block">max：该题型占满所选章节合计词数（{{ paperChapterWordCount }} 题），其余题型自动归零</p>
        </el-form-item>
        <el-form-item label="整卷限时">
          <el-input-number v-model="paperTimeLimit" :min="5" :max="120" />
          <span class="hint">分钟（超过整卷时间自动交卷）</span>
        </el-form-item>
        <el-form-item label="每词限时">
          <el-radio-group v-model="paperTimePolicy">
            <el-radio-button value="relaxed">宽松</el-radio-button>
            <el-radio-button value="standard">标准</el-radio-button>
            <el-radio-button value="tight">紧凑</el-radio-button>
          </el-radio-group>
          <span class="hint">宽松 ×1.5 · 标准 ×1.0 · 紧凑 ×0.7 —— 只改变每题倒计时，不影响得分</span>
        </el-form-item>
        <el-form-item label="及格线">
          <el-input-number v-model="paperPassScore" :min="0" :max="100" />
        </el-form-item>
        <p class="field-hint--block">
          每题倒计时按词长自动计算（短词短、长词长），可再用上面的限时档位整体放宽/收紧；
          计分口径：每题得分比 = 词序相似度（连续计分，区分大小写、空格参与比较）；<br />
          卷面分 = Σ(得分比 × 原始时限) ÷ Σ(原始时限) × 100 —— 权重恒用原始时限，
          选「宽松 / 紧凑」只影响答题时间，<b>不改变得分</b>。
        </p>
      </el-form>
      <template #footer>
        <el-button @click="paperDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="paperSaving"
          :disabled="!paperForm.name.trim() || !paperForm.chapterIds.length || paperNoWords || paperCountOver"
          @click="createPaper"
        >
          生成考核卷（共 {{ paperTotal }} 题）
        </el-button>
      </template>
    </el-dialog>

    <!-- 分配考核卷到班级 -->
    <el-dialog v-model="assignDialog" :title="`分配考核卷「${assignTarget?.name}」`" width="520px">
      <div v-if="assignTarget?.assignments?.length" class="assigned-box">
        <p class="assigned-title">已分配班级（点标签 × 可移除）</p>
        <div class="assigned-tags">
          <el-tag
            v-for="a in assignTarget.assignments"
            :key="a.id"
            :type="assignState(a).type"
            size="small"
            closable
            @close="removeAssignInDialog(a)"
          >
            {{ a.class_name || `#${a.class_id}` }} · {{ assignState(a).label }}
            <template v-if="a.max_attempts">· 限{{ a.max_attempts }}次</template>
          </el-tag>
        </div>
      </div>
      <el-form label-width="110px">
        <el-form-item label="新增分配">
          <el-select v-model="assignForm.classId" style="width: 240px" placeholder="选择班级（已分配的已禁用）">
            <el-option
              v-for="c in classes"
              :key="c.id"
              :label="c.name + (c.is_default ? '（默认）' : '')"
              :value="c.id"
              :disabled="isAssignedClass(c.id)"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="启用时间">
          <el-date-picker v-model="assignForm.available_at" type="datetime" format="YYYY-MM-DD HH:mm:ss" value-format="YYYY-MM-DD HH:mm:ss" placeholder="留空 = 立即启用" style="width: 240px" />
        </el-form-item>
        <el-form-item label="截止时间">
          <el-date-picker v-model="assignForm.expires_at" type="datetime" format="YYYY-MM-DD HH:mm:ss" value-format="YYYY-MM-DD HH:mm:ss" placeholder="留空 = 不截止" style="width: 240px" />
        </el-form-item>
        <el-form-item label="可考次数">
          <el-input-number v-model="assignForm.max_attempts" :min="1" :max="99" style="width: 240px" placeholder="留空 = 不限" />
          <span class="field-hint">每人最多开考的次数，留空 = 不限；放弃/超时也计次</span>
        </el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" title="到启用时间后，考核卷自动出现在该班级用户的考核页" />
      <template #footer>
        <el-button @click="assignDialog = false">取消</el-button>
        <el-button type="primary" @click="doAssign">确认分配</el-button>
      </template>
    </el-dialog>

    <!-- 查看考核卷题目（预览全班同卷内容） -->
    <el-drawer v-model="tqDrawer" :title="`考核卷题目 · ${tqName}`" size="62%">
      <div v-loading="tqLoading">
        <el-table :data="tqItems" size="small" max-height="calc(100vh - 140px)">
          <el-table-column prop="no" label="#" width="55" />
          <el-table-column label="题型" width="70">
            <template #default="{ row }">{{ TQ_TYPE_LABEL[row.type] || row.type }}</template>
          </el-table-column>
          <el-table-column label="题干" min-width="240">
            <template #default="{ row }">
              <span v-if="row.type !== 'choice'">{{ tqPrompt(row) }}</span>
              <span v-else>
                {{ tqPrompt(row) }}
                <div class="tq-options">
                  <span v-for="o in row.payload.options || []" :key="o.key" class="tq-option">
                    {{ o.key }}. {{ o.text }}<template v-if="o.text === row.meaning"> ✓</template>
                  </span>
                </div>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="答案" min-width="140">
            <template #default="{ row }">{{ tqAnswer(row) }}</template>
          </el-table-column>
        </el-table>
        <p v-if="!tqLoading && !tqItems.length" class="hint">该卷暂无题目数据</p>
      </div>
    </el-drawer>

    <!-- 考核口径说明（只读：口径已固化，无任何可配置项） -->
    <el-dialog v-model="examCfgDialog" title="考核口径说明（只读）" width="560px">
      <p class="field-hint" style="margin-top: 0">
        每题倒计时 = 原始时限向上取整 × 限时档位倍数：打字题 5 秒 + 每个字符 0.75 秒、选择题固定 10 秒；
        单词若单独设了「限时秒」，则替换该词打字题的原始时限。<br />
        限时档位（宽松 ×1.5 / 标准 ×1.0 / 紧凑 ×0.7）只作用于倒计时；<b>计分权重恒用原始时限</b>，档位不改变卷面分。<br />
        每题计分权重 = 该题原始时限（长题权重更高）。<br />
        卷面分 = Σ(每题得分比 × 该题原始时限) ÷ Σ(该题原始时限) × 100；
        得分比 = 词序相似度（difflib 算法，连续计分，区分大小写，无「近似」判定）。<br />
        注意：游戏的简单 / 普通 / 困难是<b>另一套独立系数</b>（0.8 / 1.0 / 1.25，写死在代码里），
        与考核无关，也不可调整；下坠快慢由「系统设置 · 游戏下坠倍数」全局控制。
      </p>
      <template #footer>
        <el-button type="primary" @click="examCfgDialog = false">知道了</el-button>
      </template>
    </el-dialog>

    <!-- 考核卷成绩 -->
    <el-dialog v-model="resultDialog" :title="`考核卷成绩 · ${resultData?.template?.name ?? ''}`" width="960px">
      <el-descriptions v-if="resultData?.template" :column="4" size="small" border style="margin-bottom: 12px">
        <el-descriptions-item label="题量">{{ resultData.template.question_count }}</el-descriptions-item>
        <el-descriptions-item label="及格线">{{ resultData.template.pass_score }}</el-descriptions-item>
        <el-descriptions-item label="整卷限时">{{ resultData.template.time_limit_min }} 分钟</el-descriptions-item>
      </el-descriptions>
      <div v-if="resultData?.summary" class="stat-row">
        <el-tag type="info">参加 {{ resultData.summary.total_students }} 人</el-tag>
        <el-tag type="success">已交卷 {{ resultData.summary.submitted }} 人</el-tag>
        <el-tag :type="resultData.summary.average === null ? 'info' : 'primary'">
          平均 {{ resultData.summary.average ?? '-' }} 分
        </el-tag>
        <el-tag>最高 {{ resultData.summary.max ?? '-' }}</el-tag>
        <el-tag>最低 {{ resultData.summary.min ?? '-' }}</el-tag>
        <el-tag :type="(resultData.summary.pass_rate ?? 0) >= 60 ? 'success' : 'warning'">
          及格率 {{ resultData.summary.pass_rate ?? '-' }}%
        </el-tag>
      </div>

      <el-tabs v-model="resultTab">
        <el-tab-pane label="学生成绩" name="students">
          <el-table v-loading="resultLoading" :data="resultData?.items ?? []" size="small" @row-click="showStudentDetail">
            <el-table-column prop="username" label="学生" min-width="120" />
            <el-table-column prop="class_name" label="班级" width="130" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="row.status === '已交卷' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最高分" width="90">
              <template #default="{ row }">
                <b :class="row.passed ? 'score-pass' : row.passed === false ? 'score-fail' : ''">{{ row.best_score ?? '-' }}</b>
              </template>
            </el-table-column>
            <el-table-column prop="last_score" label="最近一次" width="100" />
            <el-table-column label="是否及格" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.passed !== null" :type="row.passed ? 'success' : 'danger'" size="small">
                  {{ row.passed ? '及格' : '不及格' }}
                </el-tag>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column prop="duration" label="用时(s)" width="90" />
            <el-table-column prop="blur_count" label="失焦" width="80" />
            <el-table-column label="次数" width="90">
              <template #default="{ row }">{{ row.attempts }} / 开考 {{ row.opened }}</template>
            </el-table-column>
            <el-table-column prop="submitted_at" label="交卷时间" width="170" />
            <el-table-column label="操作" width="90">
              <template #default="{ row }">
                <el-button size="small" text type="primary" :disabled="!row.best_record_id" @click.stop="showStudentDetail(row)">
                  逐题明细
                </el-button>
              </template>
            </el-table-column>
          </el-table>
          <p class="field-hint">点击任意一行可查看该学生的逐题作答明细（默认展示其最高分那次）</p>
        </el-tab-pane>

        <el-tab-pane label="题目分析" name="questions">
          <p v-if="questionStats" class="field-hint">共 {{ questionStats.submissions }} 份有效答卷 · 点「作答明细」可查看每题各学生的实际作答</p>
          <el-table v-loading="resultLoading" :data="questionStats?.items ?? []" size="small">
            <el-table-column prop="index" label="题号" width="60" />
            <el-table-column label="题型" width="80">
              <template #default="{ row }">{{ { dictation: '默写', choice: '选择', cloze: '挖空', listening: '听音' }[row.type] || row.type }}</template>
            </el-table-column>
            <el-table-column prop="spelling" label="单词" min-width="140" />
            <el-table-column prop="limit_s" label="限时(s)" width="90" />
            <el-table-column label="答题人数" width="90">
              <template #default="{ row }">
                <span :class="row.answered ? '' : 'muted'">{{ row.answered }}</span>
              </template>
            </el-table-column>
            <el-table-column label="平均得分比" width="110">
              <template #default="{ row }">
                <span :class="rateClass(row.score_rate)">{{ row.score_rate === null ? '-' : row.score_rate + '%' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="正确率" width="100">
              <template #default="{ row }">
                <span :class="rateClass(row.correct_rate)">{{ row.correct_rate === null ? '-' : row.correct_rate + '%' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="correct" label="正确" width="70" />
            <el-table-column prop="near" label="近似" width="70" />
            <el-table-column prop="wrong" label="错误" width="70" />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button size="small" text type="primary" :disabled="!row.answered" @click="openQuestionAnswers(row)">
                  作答明细
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>

      <!-- 学生逐题明细 -->
      <el-drawer v-model="detailDrawer" :title="`${studentDetail?.user?.username ?? ''} · 逐题明细`" size="60%">
        <template v-if="studentDetail">
          <p class="field-hint">
            得分 {{ studentDetail.score }} · 用时 {{ studentDetail.duration }}s · 失焦 {{ studentDetail.blur_count }} 次 · {{ studentDetail.created_at }}
          </p>
          <el-table :data="studentDetail.per_question" size="small">
            <el-table-column label="题号" width="60">
              <template #default="{ $index }">{{ $index + 1 }}</template>
            </el-table-column>
            <el-table-column label="题型" width="80">
              <template #default="{ row }">{{ { dictation: '默写', choice: '选择', cloze: '挖空', listening: '听音' }[row.type] || row.type }}</template>
            </el-table-column>
            <el-table-column label="题目" min-width="160">
              <template #default="{ row }">
                <b>{{ row.spelling }}</b>
                <span class="q-meaning">{{ row.meaning }}</span>
              </template>
            </el-table-column>
            <el-table-column label="作答" min-width="130">
              <template #default="{ row }">
                <span :class="row.result === 'correct' ? 'ans-ok' : 'ans-bad'">{{ row.your_answer || '（未作答）' }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="correct_answer" label="正确答案" min-width="130" />
            <el-table-column prop="limit_s" label="限时(s)" width="90" />
            <el-table-column label="判定" width="130">
              <template #default="{ row }">
                <el-tag :type="row.result === 'correct' ? 'success' : row.result === 'near' ? 'warning' : 'danger'" size="small">
                  {{ resultLabel(row.result) }}（{{ row.raw_score }}）
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </el-drawer>

      <!-- 题目分析：单题作答明细 -->
      <el-drawer v-model="qDrawer" :title="qDetail ? `第 ${qDetail.question.index} 题 · 作答明细` : '作答明细'" size="60%">
        <template v-if="qDetail">
          <p class="field-hint q-head">
            {{ { dictation: '默写', choice: '选择', cloze: '挖空', listening: '听音' }[qDetail.question.type] || qDetail.question.type }}
            · <b>{{ qDetail.question.spelling }}</b>
            <span class="q-meaning">{{ qDetail.question.meaning }}</span>
            · 正确答案：<b class="ans-ok">{{ qDetail.question.correct_answer }}</b>
            · 限时 {{ qDetail.question.limit_s }}s
            · 共 {{ qDetail.answers.length }} 条作答
          </p>
          <el-table v-loading="qDetailLoading" :data="qDetail.answers" size="small">
            <el-table-column prop="username" label="学生" min-width="110" />
            <el-table-column label="第几次" width="80">
              <template #default="{ row }">{{ row.attempt }}</template>
            </el-table-column>
            <el-table-column label="作答" min-width="140">
              <template #default="{ row }">
                <span :class="row.result === 'correct' ? 'ans-ok' : 'ans-bad'">{{ row.your_answer || '（未作答）' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="判定" width="130">
              <template #default="{ row }">
                <el-tag :type="row.result === 'correct' ? 'success' : row.result === 'near' ? 'warning' : 'danger'" size="small">
                  {{ resultLabel(row.result) }}（{{ row.raw_score }}）
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="submitted_at" label="交卷时间" width="170" />
          </el-table>
        </template>
      </el-drawer>

      <template #footer>
        <el-button @click="resultDialog = false">关闭</el-button>
        <el-button type="primary" :disabled="!resultData" @click="downloadResultsCsv(resultData.template)">
          下载 CSV
        </el-button>
      </template>
    </el-dialog>
  </PageShell>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
  align-items: center;
}
.assigned-box {
  margin-bottom: 14px;
  padding: 10px 12px;
  border: 1px solid var(--wt-border, #e5e7eb);
  border-radius: 8px;
}
.assigned-title {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--wt-text-4, #9ca3af);
}
.assigned-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
/* 题型 × 题量：竖排行（与 ExamView 考核组卷一致） */
.tc-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tc-line {
  display: flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
}
.tc-label {
  min-width: 32px;
  color: var(--wt-text-3);
}
.tc-col .el-input-number {
  width: 130px;
}
.field-hint--warn {
  color: var(--wt-danger);
}
/* 表单项下方的整行说明文字 */
.field-hint--block {
  width: 100%;
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--wt-text-4);
}
/* 考核成绩弹窗 */
.stat-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.score-pass {
  color: var(--wt-success, #22c55e);
}
.score-fail {
  color: var(--wt-danger, #ef4444);
}
.rate-high {
  color: var(--wt-success, #22c55e);
}
.rate-mid {
  color: var(--wt-warning, #f59e0b);
}
.rate-low {
  color: var(--wt-danger, #ef4444);
}
.q-meaning {
  margin-left: 8px;
  font-size: 12px;
  color: var(--wt-text-4, #9ca3af);
}
.ans-ok {
  color: var(--wt-success, #22c55e);
}
.ans-bad {
  color: var(--wt-danger, #ef4444);
}
.muted {
  color: var(--wt-text-4, #9ca3af);
}
.q-head {
  margin: 0 0 10px;
  line-height: 1.9;
}
.ai-card {
  max-width: 640px;
}
.hint {
  font-size: 12px;
  color: var(--wt-text-4);
}
.tq-options {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--wt-text-3, #6b7280);
}
.tq-option {
  white-space: nowrap;
}
.field-hint {
  font-size: 12px;
  color: var(--wt-text-4);
  margin-left: 10px;
}
.tol-unit {
  margin: 0 6px;
  font-size: 13px;
  color: var(--wt-text-3);
  white-space: nowrap;
}
.spacer {
  flex: 1;
}
</style>
