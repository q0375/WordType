/** 按域模块化 API —— 与后端 /api/v1 契约一一对应（snake_case 字段）。 */
import http from './http';

// ---------- auth ----------
export const authApi = {
  login: (username: string, password: string) => http.post('/auth/login', { username, password }),
  register: (username: string, password: string, invite_code?: string) =>
    http.post('/auth/register', { username, password, invite_code }),
  logout: () => http.post('/auth/logout'),
  changePassword: (old_password: string, new_password: string) =>
    http.post('/auth/password', { old_password, new_password }),
  deactivate: () => http.post('/account/deactivate'),
};

// ---------- settings ----------
export const settingsApi = {
  get: () => http.get('/settings'),
  put: (patch: Record<string, unknown>) => http.put('/settings', patch),
};

// ---------- books ----------
export const booksApi = {
  list: (tab = 'all', q = '', page = 1) => http.get('/books', { params: { tab, q, page } }),
  create: (name: string) => http.post('/books', { name }),
  patch: (id: number, name: string) => http.patch(`/books/${id}`, { name }),
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
  importConfirm: (token: string, duplicate_strategy: 'skip' | 'overwrite', words_per_chapter?: number | null, unit_count?: number | null) =>
    http.post('/books/import/confirm', null, {
      params: {
        token,
        duplicate_strategy,
        ...(words_per_chapter ? { words_per_chapter } : {}),
        ...(unit_count ? { unit_count } : {}),
      },
    }),
};

// ---------- study ----------
export const studyApi = {
  next: (scope?: string) => http.get('/study/next', { params: scope ? { scope } : {} }),
  selfRate: (word_id: number, rating: 'know' | 'vague' | 'unknown', request_id: string) =>
    http.post('/study/self-rate', { word_id, rating, request_id }),
  dictation: (word_id: number, typed: string, duration_ms: number, detail: any, request_id: string) =>
    http.post('/study/dictation', { word_id, typed, duration_ms, detail, request_id }),
  savePosition: (chapter_id: number, last_word_index: number) =>
    http.post('/study/position', null, { params: { chapter_id, last_word_index } }),
};

// ---------- practice ----------
export const practiceApi = {
  session: (chapter_ids: number[], types: string[], group_size: number) =>
    http.post('/practice/session', { chapter_ids, types, group_size }),
  answer: (payload: { qid: string; word_id: number; type: string; typed?: string; choice_key?: string; duration_ms: number; detail?: any; request_id: string }) =>
    http.post('/practice/answer', payload),
};

// ---------- review ----------
export const reviewApi = {
  today: () => http.get('/review/today'),
  answer: (payload: { word_id: number; form: 'typing' | 'choice' | 'self'; typed?: string; choice_key?: string; rating?: string; reshow?: number; duration_ms?: number; detail?: any; request_id: string }) =>
    http.post('/review/answer', payload),
};

// ---------- exam ----------
export const examApi = {
  start: (payload: { chapter_id: number; type_counts: Record<string, number>; time_limit_min: number; pass_score: number; loose_match: boolean }) =>
    http.post('/exam/start', payload),
  blur: (paper_id: string, count = 1) => http.post(`/exam/${paper_id}/blur`, { count }),
  submit: (paper_id: string, answers: { qid: string; typed?: string; choice_key?: string }[], duration_ms: number, idempotencyKey: string) =>
    http.post('/exam/submit', { paper_id, answers, duration_ms }, { headers: { 'Idempotency-Key': idempotencyKey } }),
  void: (paper_id: string) => http.post(`/exam/${paper_id}/void`),
  records: (page = 1) => http.get('/exam/records', { params: { page } }),
  record: (id: number) => http.get(`/exam/records/${id}`),
};

// ---------- game ----------
export const gameApi = {
  start: (chapter_ids: number[], difficulty: string, mode: 'endless' | 'timed') =>
    http.post('/game/start', { chapter_ids, difficulty, mode }),
  submit: (
    payload: { session_id: string; difficulty: string; mode: string; duration_ms: number; score: number; max_combo: number; correct_count: number; words: any[] },
    idempotencyKey: string,
  ) => http.post('/game/submit', payload, { headers: { 'Idempotency-Key': idempotencyKey } }),
};

// ---------- wrongbook ----------
export const wrongbookApi = {
  list: (params: { resolved?: number; source?: string; pinned?: number; page?: number }) =>
    http.get('/wrongbook', { params }),
  add: (word_id: number) => http.post('/wrongbook', { word_id }),
  patch: (word_id: number, patch: { pinned?: number; resolved?: number }) =>
    http.patch(`/wrongbook/${word_id}`, patch),
};

// ---------- stats / advice ----------
export const statsApi = {
  dashboard: () => http.get('/stats/dashboard'),
  trend: (metric: 'wpm' | 'accuracy', source = 'all', days = 30) =>
    http.get('/stats/trend', { params: { metric, source, days } }),
};

export const adviceApi = {
  get: () => http.get('/advice'),
  refresh: () => http.post('/advice/refresh'),
};

// ---------- export / admin ----------
export const exportApi = {
  accountUrl: '/api/v1/export/account',
};

export const aiConfigApi = {
  get: () => http.get('/ai-config'),
  put: (patch: Record<string, unknown>) => http.put('/ai-config', patch),
  test: () => http.post('/ai-config/test'),
};

export const adminApi = {
  users: (params: { q?: string; status?: string; page?: number }) => http.get('/admin/users', { params }),
  resetPassword: (id: number) => http.post(`/admin/users/${id}/reset-password`),
  restore: (id: number) => http.post(`/admin/users/${id}/restore`),
  inviteCodes: (params: { status?: string; page?: number }) => http.get('/admin/invite-codes', { params }),
  createInviteCodes: (count: number, valid_days: number) => http.post('/admin/invite-codes', { count, valid_days }),
  patchInviteCode: (id: number, is_active: number) => http.patch(`/admin/invite-codes/${id}`, { is_active }),
  devdataSummary: (userId?: number) => http.get('/admin/devdata/summary', { params: userId ? { user_id: userId } : {} }),
  devdataSeed: (patch: Record<string, unknown>) => http.post('/admin/devdata/seed', patch),
  devdataClear: (userId?: number) => http.post('/admin/devdata/clear', userId ? { target_user_id: userId } : {}),
};
