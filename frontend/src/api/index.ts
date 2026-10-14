/** 按域模块化 API —— 与后端 /api/v1 契约一一对应（snake_case 字段）。 */
import http from './http';

// ---------- auth ----------
export const authApi = {
  login: (username: string, password: string) => http.post('/auth/login', { username, password }),
  register: (username: string, password: string, invite_code?: string) =>
    http.post('/auth/register', { username, password, invite_code }),
  // 显式携带 token：axios 请求拦截器在微任务中才读 store，而 auth.logout() 同步清 token，
  // 依赖拦截器附带认证头存在竞态（logout 请求丢头 → 401 且服务端未吊销 jti）
  logout: (token?: string) =>
    http.post('/auth/logout', null, token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
  changePassword: (old_password: string, new_password: string) =>
    http.post('/auth/password', { old_password, new_password }),
  // 2026-09-22 起用户名不可修改；昵称是唯一可改的展示名
  changeNickname: (nickname: string) => http.post('/auth/nickname', { nickname }),
  deactivate: () => http.post('/account/deactivate'),
};

// ---------- settings ----------
export const settingsApi = {
  get: () => http.get('/settings'),
  put: (patch: Record<string, unknown>) => http.put('/settings', patch),
};

// ---------- books ----------
export const booksApi = {
  list: (tab = 'all', q = '', page = 1, pageSize = 20) => http.get('/books', { params: { tab, q, page, page_size: pageSize } }),
  create: (name: string) => http.post('/books', { name }),
  patch: (id: number, data: { name?: string; is_public?: boolean }) => http.patch(`/books/${id}`, data),
  remove: (id: number) => http.delete(`/books/${id}`),
  clone: (id: number) => http.post(`/books/${id}/clone`),
  exportUrl: (id: number) => `/api/v1/books/${id}/export`,
  /** 带鉴权下载导出 CSV（<a href> 无法携带 Authorization 头） */
  exportCsv: async (id: number, name: string) => {
    const resp = await http.get(`/books/${id}/export`, { responseType: 'blob' });
    const url = URL.createObjectURL(resp.data);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  },
  clearChapters: (id: number) => http.delete(`/books/${id}/chapters`),
  resplit: (id: number, words_per_chapter?: number | null, unit_count?: number | null) =>
    http.post(`/books/${id}/resplit`, null, {
      params: {
        ...(words_per_chapter ? { words_per_chapter } : {}),
        ...(unit_count ? { unit_count } : {}),
      },
    }),
  chapters: (id: number) => http.get(`/books/${id}/chapters`),
  createChapter: (book_id: number, name: string, sort_order = 0) =>
    http.post('/chapters', { book_id, name, sort_order }),
  patchChapter: (id: number, patch: { name?: string; sort_order?: number }) =>
    http.patch(`/chapters/${id}`, patch),
  deleteChapter: (id: number) => http.delete(`/chapters/${id}`),
  words: (book_id: number, params: { chapter_id?: number; q?: string; page?: number; page_size?: number }) =>
    http.get('/words', { params: { book_id, ...params } }),
  createWord: (payload: { chapter_id: number; spelling: string; meaning: string; phonetic?: string; example?: string }) =>
    http.post('/words', payload),
  patchWord: (id: number, patch: Record<string, unknown>) => http.patch(`/words/${id}`, patch),
  deleteWord: (id: number) => http.delete(`/words/${id}`),
  importUpload: (form: FormData) => http.post('/books/import', form),
  importJob: (jobId: number) => http.get(`/books/import/${jobId}`),
  importPreview: (token: string) => http.get('/books/import/preview', { params: { token } }),
  importConfirm: (token: string, duplicate_strategy: 'skip' | 'overwrite', words_per_chapter?: number | null, unit_count?: number | null, unit_mode?: string) =>
    http.post('/books/import/confirm', null, {
      params: {
        token,
        duplicate_strategy,
        ...(words_per_chapter ? { words_per_chapter } : {}),
        ...(unit_count ? { unit_count } : {}),
        ...(unit_mode ? { unit_mode } : {}),
      },
    }),
};

// ---------- study ----------
export const studyApi = {
  next: (scope?: string, force = false, exclude: number[] = []) =>
    http.get('/study/next', {
      params: {
        ...(scope ? { scope } : {}),
        ...(force ? { force: true } : {}),
        ...(force && exclude.length ? { exclude: exclude.join(',') } : {}),
      },
    }),
  selfRate: (word_id: number, rating: 'know' | 'vague' | 'unknown', request_id: string) =>
    http.post('/study/self-rate', { word_id, rating, request_id }),
  dictation: (word_id: number, typed: string, duration_ms: number, detail: any, request_id: string) =>
    http.post('/study/dictation', { word_id, typed, duration_ms, detail, request_id }),
  savePosition: (chapter_id: number, last_word_index: number) =>
    http.post('/study/position', null, { params: { chapter_id, last_word_index } }),
};

// ---------- practice ----------
export const practiceApi = {
  session: (chapter_ids: number[], types: string[], group_size: number, word_ids: number[] = []) =>
    http.post('/practice/session', { chapter_ids, types, group_size, word_ids }),
  answer: (payload: { qid: string; word_id: number; type: string; typed?: string; choice_key?: string; duration_ms: number; detail?: any; request_id: string }) =>
    http.post('/practice/answer', payload),
};

// ---------- review ----------
export const reviewApi = {
  today: () => http.get('/review/today'),
  plan: (days = 14) => http.get('/review/plan', { params: { days } }),
  advance: (target_date: string) => http.post('/review/advance', { target_date }),
  answer: (payload: { word_id: number; form: 'typing' | 'choice' | 'self'; typed?: string; choice_key?: string; rating?: string; reshow?: number; duration_ms?: number; detail?: any; request_id: string }) =>
    http.post('/review/answer', payload),
};

// ---------- exam ----------
export const examApi = {
  // loose_match（2026-09-19）与 time_policy（2026-09-20）均已取消，不再下发
  // 多章节复选（2026-09-22）：chapter_ids
  start: (payload: { chapter_ids: number[]; type_counts: Record<string, number>; time_limit_min: number; pass_score: number; time_policy: string }) =>
    http.post('/exam/start', payload),
  blur: (paper_id: string, count = 1) => http.post(`/exam/${paper_id}/blur`, { count }),
  submit: (paper_id: string, answers: { qid: string; typed?: string; choice_key?: string }[], duration_ms: number, idempotencyKey: string) =>
    http.post('/exam/submit', { paper_id, answers, duration_ms }, { headers: { 'Idempotency-Key': idempotencyKey } }),
  void: (paper_id: string) => http.post(`/exam/${paper_id}/void`),
  active: () => http.get('/exam/active'),
  assigned: () => http.get('/exam/assigned'),
  startAssigned: (templateId: number) => http.post(`/exam/assigned/${templateId}/start`),
  records: (page = 1) => http.get('/exam/records', { params: { page } }),
  record: (id: number) => http.get(`/exam/records/${id}`),
};

// ---------- game ----------
export const gameApi = {
  start: (chapter_ids: number[], difficulty: string, mode: 'endless' | 'timed', book_id?: number | null) =>
    // 排行榜分组键由后端按 chapter_ids 规范化（单选=章节榜，多选=组合专属榜，全选/不选=整本榜）
    http.post('/game/start', { chapter_ids, difficulty, mode, book_id }),
  submit: (
    payload: { session_id: string; difficulty: string; mode: string; duration_ms: number; score: number; max_combo: number; correct_count: number; words: any[] },
    idempotencyKey: string,
  ) => http.post('/game/submit', payload, { headers: { 'Idempotency-Key': idempotencyKey } }),
  leaderboard: (params: { book_id: number; mode: string; difficulty: string; chapter_ids?: string }) =>
    http.get('/game/leaderboard', { params }),
};

// ---------- wrongbook ----------
export const wrongbookApi = {
  list: (params: { resolved?: number; source?: string; pinned?: number; page?: number }) =>
    http.get('/wrongbook', { params }),
  add: (word_id: number) => http.post('/wrongbook', { word_id }),
  patch: (word_id: number, patch: { pinned?: number; resolved?: number }) =>
    http.patch(`/wrongbook/${word_id}`, patch),
  // 批量操作（2026-10-04 批量移出）：仅本人条目，返回 { updated }
  batchPatch: (word_ids: number[], patch: { pinned?: number; resolved?: number }) =>
    http.patch('/wrongbook', { word_ids, ...patch }),
};

// ---------- stats / advice ----------
export const statsApi = {
  dashboard: () => http.get('/stats/dashboard'),
  trend: (metric: 'wpm' | 'accuracy' | 'volume', days = 30) =>
    http.get('/stats/trend', { params: { metric, days } }),
};

export const adviceApi = {
  get: () => http.get('/advice'),
  refresh: () => http.post('/advice/refresh'),
};

// ---------- export / admin ----------
export const exportApi = {
  // 个人数据导出：必须走 axios（带 Authorization 头），<a href> 导航带不上 token 会 401
  account: () => http.get('/export/account', { responseType: 'blob', timeout: 120000 }),
};

export const aiConfigApi = {
  get: () => http.get('/ai-config'),
  put: (patch: Record<string, unknown>) => http.put('/ai-config', patch),
  test: () => http.post('/ai-config/test'),
};

export const adminApi = {
  users: (params: { q?: string; status?: string; page?: number }) => http.get('/admin/users', { params }),
  importUsers: (users: { username: string; password?: string; class_name?: string }[]) =>
    http.post('/admin/users/import', { users }),
  resetPassword: (id: number, new_password?: string) => http.post(`/admin/users/${id}/reset-password`, new_password ? { new_password } : {}),
  restore: (id: number) => http.post(`/admin/users/${id}/restore`),
  inviteCodes: (params: { status?: string; page?: number }) => http.get('/admin/invite-codes', { params }),
  createInviteCodes: (count: number, valid_days: number) => http.post('/admin/invite-codes', { count, valid_days }),
  patchInviteCode: (id: number, is_active: number) => http.patch(`/admin/invite-codes/${id}`, { is_active }),
  devdataSummary: (userId?: number) => http.get('/admin/devdata/summary', { params: userId ? { user_id: userId } : {} }),
  devdataSeed: (patch: Record<string, unknown>) => http.post('/admin/devdata/seed', patch),
  devdataClear: (userId?: number) => http.post('/admin/devdata/clear', userId ? { target_user_id: userId } : {}),
  // 班级（D34）
  classes: () => http.get('/admin/classes'),
  createClass: (name: string) => http.post('/admin/classes', { name }),
  renameClass: (id: number, name: string) => http.patch(`/admin/classes/${id}`, { name }),
  deleteClass: (id: number) => http.delete(`/admin/classes/${id}`),
  setUserClass: (userId: number, classId: number | null) => http.patch(`/admin/users/${userId}/class`, { class_id: classId }),
  // 考核卷（D36）
  examPapers: () => http.get('/admin/exam-papers'),
  createExamPaper: (payload: Record<string, unknown>) => http.post('/admin/exam-papers', payload),
  patchExamPaper: (id: number, patch: Record<string, unknown>) => http.patch(`/admin/exam-papers/${id}`, patch),
  deleteExamPaper: (id: number) => http.delete(`/admin/exam-papers/${id}`),
  assignPaper: (templateId: number, payload: { class_id: number; available_at?: string | null; expires_at?: string | null }) =>
    http.post(`/admin/exam-papers/${templateId}/assign`, payload),
  deleteAssignment: (id: number) => http.delete(`/admin/assigns/${id}`),
  examTemplateQuestions: (templateId: number) => http.get(`/admin/exam-templates/${templateId}/questions`),
  // 考核口径已全部固化、无接口可配（得分比 2026-09-19 取消；每词限时档位与倍数 2026-09-20 取消）
  // 游戏节奏（全局唯一可配口径项）：下坠倍数，出现→消失 = 时限 × mult
  setGameFallMult: (mult: number) => http.put('/admin/game-config/fall-mult', { mult }),
  // 考核卷成绩（查看 / 导出）
  paperResults: (templateId: number) => http.get(`/admin/exam-papers/${templateId}/results`),
  paperQuestionStats: (templateId: number) => http.get(`/admin/exam-papers/${templateId}/question-stats`),
  paperQuestionAnswers: (templateId: number, qid: string) => http.get(`/admin/exam-papers/${templateId}/questions/${qid}/answers`),
  paperResultsCsv: (templateId: number) => http.get(`/admin/exam-papers/${templateId}/results.csv`, { responseType: 'blob' }),
  adminExamRecord: (recordId: number) => http.get(`/admin/exam-records/${recordId}`),
};
