<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { confirmAction } from '../../core/confirm';
import { booksApi } from '../../api';
import { useAuthStore } from '../../stores/auth';

const router = useRouter();

const auth = useAuthStore();
const tab = ref<'all' | 'mine' | 'public'>('all');
const q = ref('');
const bookPage = ref(1);
const BOOK_PAGE_SIZE = 20;
const booksTotal = ref(0);
const books = ref<any[]>([]);
const loading = ref(false);

const chaptersDialog = ref(false);
const currentBook = ref<any>(null);
const chapters = ref<any[]>([]);
const wordsDialog = ref(false);
const currentChapter = ref<any>(null);
const words = ref<any[]>([]);
const wordsTotal = ref(0);
const wordQuery = ref('');
const wordPage = ref(1);
const WORD_PAGE_SIZE = 50;

const importDialog = ref(false);
const importStep = ref<'upload' | 'preview'>('upload');
const importFile = ref<File | null>(null);
const importMode = ref<'new' | 'existing'>('new');
const importBookName = ref('');
const importTarget = ref<any>(null);
const importPreview = ref<any>(null);
const dupStrategy = ref<'skip' | 'overwrite'>('skip');
const unitMode = ref<'file' | 'none' | 'by-size' | 'by-count'>('file');
const unitSize = ref<number>(30); // 每单元词数
const unitCount = ref<number>(10); // 单元数量

const wordForm = ref({ spelling: '', meaning: '', phonetic: '', example: '', time_limit_s: null as number | null });

onMounted(load);

async function load() {
  loading.value = true;
  try {
    const { data } = await booksApi.list(tab.value, q.value, bookPage.value);
    books.value = data.items;
    booksTotal.value = data.total;
  } finally {
    loading.value = false;
  }
}

function onBookPageChange(p: number) {
  bookPage.value = p;
  load();
}

async function renameBook(b: any) {
  const { value } = await ElMessageBox.prompt('新名称', '重命名词库', { inputValue: b.name });
  await booksApi.patch(b.id, { name: value });
  load();
}

async function togglePublic(b: any, v: boolean) {
  try {
    await booksApi.patch(b.id, { is_public: v });
    b.is_public = v ? 1 : 0;
    ElMessage.success({ message: v ? `「${b.name}」已公开，所有用户可见` : `「${b.name}」已设为私有，仅自己可见`, duration: 2000, showClose: true });
  } catch (e: any) {
    ElMessage.error({ message: e?.message || '设置失败', duration: 2000, showClose: true });
  }
}

async function removeBook(b: any) {
  await ElMessageBox.confirm(`将级联软删词库「${b.name}」及其章节与单词（统计保留），确认？`, '删除词库', { type: 'warning' });
  await booksApi.remove(b.id);
  ElMessage.success({ message: '已删除', duration: 2000, showClose: true });
  load();
}

async function cloneBook(b: any) {
  const { data } = await booksApi.clone(b.id);
  load();
  await ElMessageBox.confirm(
    `已在「我的词库」创建副本「${b.name}-副本」，现在就去学几个词？`,
    '克隆成功',
    { confirmButtonText: '去学习', cancelButtonText: '留在这里', type: 'success' },
  ).then(() => router.push('/study')).catch(() => {});
}

function canManage(b: any) {
  return b.owner_id === auth.user?.id || auth.isAdmin;
}

// ---------- 章节 / 单词 ----------
async function openChapters(b: any) {
  currentBook.value = b;
  const { data } = await booksApi.chapters(b.id);
  chapters.value = data.items;
  chaptersDialog.value = true;
}

async function addChapter() {
  const { value } = await ElMessageBox.prompt('章节名称', '新增章节');
  await booksApi.createChapter(currentBook.value.id, value);
  openChapters(currentBook.value);
}

async function removeChapter(ch: any) {
  await ElMessageBox.confirm(`删除章节「${ch.name}」？章节内单词会保留，可用「重新划分单元」再次分组。`, '删除章节', { type: 'warning' });
  await booksApi.deleteChapter(ch.id);
  openChapters(currentBook.value);
}

async function exportCsv(b: any) {
  await booksApi.exportCsv(b.id, b.name);
  ElMessage.success({ message: '已开始下载', duration: 2000, showClose: true });
}

async function clearAllChapters() {
  const b = currentBook.value;
  await ElMessageBox.confirm(
    `将删除词库「${b.name}」的全部章节，单词会保留，之后可用「重新划分单元」再次分组。确认？`,
    '一键清空全部章节',
    { type: 'warning', confirmButtonText: '清空章节', confirmButtonClass: 'el-button--danger' },
  );
  const { data } = await booksApi.clearChapters(b.id);
  ElMessage.success({ message: `已删除 ${data.deleted_chapters} 个章节，${data.kept_words ?? 0} 个单词已保留`, duration: 2000, showClose: true });
  openChapters(b);
  load();
}

const resplitDialog = ref(false);
const resplitMode = ref<'by-size' | 'by-count'>('by-size');
const resplitSize = ref(30);
const resplitCount = ref(10);

async function doResplit() {
  const b = currentBook.value;
  // 重新划分 = 删除全部现有章节并把单词随机重分（单词不丢，但章节/学习进度归属会变）——二次确认
  // 审计补漏 2026-10-03
  const ok = await confirmAction({
    title: '重新划分单元',
    message: `将删除词库「${b.name}」现有的全部章节，并把 ${b.word_count ?? ''} 个单词随机打散重新分组。确定继续？`,
    confirmText: '重新划分',
    cancelText: '取消',
  });
  if (!ok) return;
  const bySize = resplitMode.value === 'by-size' ? resplitSize.value : null;
  const byCount = resplitMode.value === 'by-count' ? resplitCount.value : null;
  const { data } = await booksApi.resplit(b.id, bySize, byCount);
  ElMessage.success({ message: `已随机分成 ${data.chapter_count} 个单元（共 ${data.word_count} 词）`, duration: 2000, showClose: true });
  resplitDialog.value = false;
  openChapters(b);
  load();
}

async function openWords(ch: any) {
  currentChapter.value = ch;
  wordsDialog.value = true;
  wordPage.value = 1;
  loadWords();
}

async function loadWords() {
  const { data } = await booksApi.words(currentBook.value.id, {
    chapter_id: currentChapter.value.id,
    q: wordQuery.value || undefined,
    page: wordPage.value,
    page_size: WORD_PAGE_SIZE,
  });
  words.value = data.items;
  wordsTotal.value = data.total;
}

function onWordPageChange(p: number) {
  wordPage.value = p;
  loadWords();
}

async function addWord() {
  if (!wordForm.value.spelling || !wordForm.value.meaning) {
    ElMessage.warning({ message: '拼写与释义必填', duration: 2000, showClose: true });
    return;
  }
  try {
    await booksApi.createWord({ chapter_id: currentChapter.value.id, ...wordForm.value });
    wordForm.value = { spelling: '', meaning: '', phonetic: '', example: '', time_limit_s: null };
    loadWords();
  } catch (e: any) {
    if (e.code === 'WORD_DUPLICATE') ElMessage.warning({ message: '库内已存在该单词（忽略大小写）', duration: 2000, showClose: true });
  }
}

async function removeWord(w: any) {
  await booksApi.deleteWord(w.id);
  loadWords();
}

// ---------- 单词编辑（五字段；权限与增删一致：本人私有库 / 管理员，见 canManage） ----------
const editDialog = ref(false);
const editSaving = ref(false);
const editForm = ref({ id: 0, spelling: '', meaning: '', phonetic: '', example: '', time_limit_s: null as number | null });

function openEditWord(w: any) {
  editForm.value = {
    id: w.id,
    spelling: w.spelling ?? '',
    meaning: w.meaning ?? '',
    phonetic: w.phonetic ?? '',
    example: w.example ?? '',
    time_limit_s: w.time_limit_s ?? null,
  };
  editDialog.value = true;
}

async function saveEditWord() {
  const f = editForm.value;
  if (!f.spelling.trim() || !f.meaning.trim()) {
    ElMessage.warning({ message: '拼写与释义必填', duration: 2000, showClose: true });
    return;
  }
  editSaving.value = true;
  try {
    // 音标/例句传空串 = 清空；时限传 null = 恢复自动公式
    await booksApi.patchWord(f.id, {
      spelling: f.spelling.trim(),
      meaning: f.meaning.trim(),
      phonetic: f.phonetic.trim(),
      example: f.example.trim(),
      time_limit_s: f.time_limit_s,
    });
    ElMessage.success({ message: '已保存', duration: 2000, showClose: true });
    editDialog.value = false;
    loadWords();
  } catch (e: any) {
    if (e?.code === 'WORD_DUPLICATE') ElMessage.warning({ message: '库内已存在该单词（忽略大小写）', duration: 2000, showClose: true });
  } finally {
    editSaving.value = false;
  }
}

// ---------- 导入三步制 ----------
function onFileChange(f: any) {
  importFile.value = f.raw || f;
}

const importPaste = ref('');

async function uploadPaste() {
  if (!importPaste.value.trim()) {
    ElMessage.warning({ message: '请先粘贴单词内容', duration: 2000, showClose: true });
    return;
  }
  importFile.value = new File([importPaste.value], 'paste.txt', { type: 'text/plain' });
  await uploadImport();
}

function downloadTemplate() {
  const csv = [
    '章节,单词,释义,音标,例句,时限秒',
    '英语单词,chair,椅子,,The chair is comfortable.,3',
    '英语单词,apple,苹果,/ˈæpl/,I eat an apple every day.,3',
    '英语单词,abandon,放弃,/əˈbændən/,Never abandon your dreams.,4',
  ].join('\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '词库导入模板.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

async function uploadImport() {
  if (!importFile.value) {
    ElMessage.warning({ message: '请选择 csv/txt/xlsx 文件（≤10MB，≤20000 行）', duration: 2000, showClose: true });
    return;
  }
  if (importMode.value === 'new' && !importBookName.value.trim()) {
    ElMessage.warning({ message: '请输入新词库名称', duration: 2000, showClose: true });
    return;
  }
  if (importMode.value === 'existing' && !importTarget.value) {
    ElMessage.warning({ message: '请选择目标词库', duration: 2000, showClose: true });
    return;
  }
  const fd = new FormData();
  fd.append('file', importFile.value);
  if (importMode.value === 'new') fd.append('book_name', importBookName.value.trim());
  else fd.append('target_book_id', String(importTarget.value.id));
  const { data } = await booksApi.importUpload(fd);
  // 轮询 job（2s 间隔，≤150 次）
  let job = data;
  for (let i = 0; i < 150 && job.status === 'parsing'; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    job = (await booksApi.importJob(data.job_id)).data;
  }
  importPreview.value = await booksApi.importPreview(job.preview_token).then((r: any) => r.data);
  // 文件带单元列时默认走「按文件单元」，否则退回单章节
  unitMode.value = importPreview.value.has_unit_column ? 'file' : 'none';
  importStep.value = 'preview';
}

async function confirmImport() {
  const map: Record<string, string> = { file: 'file', none: 'none', 'by-size': 'size', 'by-count': 'count' };
  const bySize = unitMode.value === 'by-size' ? unitSize.value : null;
  const byCount = unitMode.value === 'by-count' ? unitCount.value : null;
  const { data } = await booksApi.importConfirm(importPreview.value.preview_token ?? importPreview.value.job_id, dupStrategy.value, bySize, byCount, map[unitMode.value]);
  ElMessage.success({ message: data.unit_mode === 'file'
      ? `入库完成：新增 ${data.ok_rows} 词，按文件单元建成/归入 ${data.chapter_count} 个单元（顺序原样保留）`
      : data.chapter_count
        ? `入库完成：新增 ${data.ok_rows} 词，随机分成 ${data.chapter_count} 个单元`
        : `入库完成：新增 ${data.ok_rows}，覆盖 ${data.overwritten ?? 0}，错误跳过 ${data.error_rows}`, duration: 2000, showClose: true });
  importDialog.value = false;
  importStep.value = 'upload';
  load();
}
</script>

<template>
  <PageShell title="词库管理" subtitle="词库 → 章节 → 单词 · 三步导入 · 克隆/导出">
    <div class="toolbar">
      <el-radio-group v-model="tab" @change="bookPage = 1; load()">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="mine">我的</el-radio-button>
        <el-radio-button value="public">公共</el-radio-button>
      </el-radio-group>
      <el-input v-model="q" placeholder="搜索词库" style="width: 220px" clearable @keyup.enter="bookPage = 1; load()" />
      <el-button @click="bookPage = 1; load()">搜索</el-button>
      <div class="spacer"></div>
      <el-button type="success" @click="importDialog = true">导入新词库</el-button>
    </div>

    <div class="book-grid" v-loading="loading">
      <el-card v-for="b in books" :key="b.id" shadow="never" class="book-card">
        <div class="book-head">
          <h3>{{ b.name }}</h3>
          <el-tag v-if="b.is_public" type="success" size="small">公共</el-tag>
          <span v-if="auth.isAdmin && canManage(b)" class="pub-ctl" :title="'切换词库公开/私有'">
            <el-switch
              size="small"
              :model-value="!!b.is_public"
              @change="(v: any) => togglePublic(b, v)"
            />
            <span class="pub-txt">{{ b.is_public ? '公开' : '私有' }}</span>
          </span>
        </div>
        <p class="meta">{{ b.chapter_count }} 章节 · {{ b.word_count }} 词</p>
        <div class="ops">
          <el-button size="small" @click="openChapters(b)">章节/单词</el-button>
          <el-button size="small" @click="exportCsv(b)">导出 CSV</el-button>
          <el-button v-if="!canManage(b)" size="small" type="primary" plain @click="cloneBook(b)">克隆</el-button>
          <template v-if="canManage(b)">
            <el-button size="small" @click="renameBook(b)">重命名</el-button>
            <el-button size="small" type="danger" plain @click="removeBook(b)">删除</el-button>
          </template>
        </div>
      </el-card>
    </div>
    <div style="margin-top: 12px; display: flex; justify-content: flex-end">
      <el-pagination
        v-model:current-page="bookPage"
        :page-size="BOOK_PAGE_SIZE"
        :total="booksTotal"
        layout="total, prev, pager, next"
        background
        hide-on-single-page
        @current-change="onBookPageChange"
      />
    </div>

    <!-- 章节管理 -->
    <el-dialog v-model="chaptersDialog" :title="`章节管理 · ${currentBook?.name}`" width="640px">
      <div style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap">
        <template v-if="currentBook && canManage(currentBook)">
          <el-button size="small" type="primary" @click="addChapter">新增章节</el-button>
          <el-button size="small" @click="resplitDialog = true">重新划分单元</el-button>
          <el-button size="small" type="danger" plain @click="clearAllChapters">一键清空全部章节</el-button>
        </template>
        <el-tag v-else-if="currentBook" type="info" size="small">公开词库只读，克隆后可管理章节</el-tag>
      </div>
      <el-table :data="chapters" size="small">
        <el-table-column prop="name" label="章节" />
        <el-table-column prop="word_count" label="词数" width="80" />
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="openWords(row)">单词</el-button>
            <el-button v-if="currentBook && canManage(currentBook)" size="small" type="danger" plain @click="removeChapter(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 重新划分单元 -->
    <el-dialog v-model="resplitDialog" :title="`重新划分单元 · ${currentBook?.name}`" width="480px">
      <el-alert type="warning" :closable="false" title="现有章节将被删除，单词保留并随机打散重新分入新单元" style="margin-bottom: 14px" />
      <el-radio-group v-model="resplitMode">
        <el-radio value="by-size">按每单元词数</el-radio>
        <el-radio value="by-count">按单元数量</el-radio>
      </el-radio-group>
      <div v-if="resplitMode === 'by-size'" style="margin-top: 12px; display: flex; align-items: center; gap: 10px">
        <el-input-number v-model="resplitSize" :min="5" :max="200" :step="5" />
        <span>词 / 单元</span>
        <el-button v-for="n in [10, 20, 30, 50, 100]" :key="n" size="small" @click="resplitSize = n">{{ n }}</el-button>
      </div>
      <div v-if="resplitMode === 'by-count'" style="margin-top: 12px; display: flex; align-items: center; gap: 10px">
        <el-input-number v-model="resplitCount" :min="2" :max="500" :step="1" />
        <span>个单元（词数尽量均分）</span>
      </div>
      <template #footer>
        <el-button @click="resplitDialog = false">取消</el-button>
        <el-button type="primary" @click="doResplit">确认重新划分</el-button>
      </template>
    </el-dialog>

    <!-- 单词管理 -->
    <el-dialog v-model="wordsDialog" :title="`单词管理 · ${currentChapter?.name}（共 ${wordsTotal}）`" width="920px">
      <el-input v-model="wordQuery" placeholder="搜索拼写/释义" clearable style="margin-bottom: 12px" @input="wordPage = 1; loadWords()" />
      <el-tag v-if="currentBook && !canManage(currentBook)" type="info" size="small" style="margin-bottom: 12px">公开词库只读：不能增删改单词，克隆后可编辑</el-tag>
      <div v-else class="word-form">
        <el-input v-model="wordForm.spelling" placeholder="单词" style="width: 140px" />
        <el-input v-model="wordForm.meaning" placeholder="释义" style="width: 180px" />
        <el-input v-model="wordForm.phonetic" placeholder="音标（选填）" style="width: 140px" />
        <el-input v-model="wordForm.example" placeholder="例句（选填）" style="width: 150px" />
        <el-input-number v-model="wordForm.time_limit_s" :min="3" :max="600" placeholder="自动" controls-position="right" style="width: 110px" />
        <el-button type="primary" @click="addWord">添加</el-button>
      </div>
      <p v-if="currentBook && canManage(currentBook)" class="word-limit-hint">限时(秒) = 该词打字题的基础时长，覆盖默认公式「5 + 0.75×词长」；留空 = 自动。选择题固定 10 秒不受影响；该值同时用作考核计分权重（长题权重更高）。</p>
      <el-table :data="words" size="small" max-height="360">
        <el-table-column prop="spelling" label="单词" width="130" />
        <el-table-column prop="meaning" label="释义" min-width="120" show-overflow-tooltip />
        <el-table-column prop="phonetic" label="音标" width="110">
          <template #default="{ row }">{{ row.phonetic || '-' }}</template>
        </el-table-column>
        <el-table-column prop="example" label="例句" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.example || '-' }}</template>
        </el-table-column>
        <el-table-column label="限时(秒)" width="90">
          <template #default="{ row }">{{ row.time_limit_s ?? '自动' }}</template>
        </el-table-column>
        <el-table-column v-if="currentBook && canManage(currentBook)" label="操作" width="115">
          <template #default="{ row }">
            <el-button size="small" @click="openEditWord(row)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="removeWord(row)">删</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div style="margin-top: 10px; display: flex; justify-content: flex-end">
        <el-pagination
          v-model:current-page="wordPage"
          :page-size="WORD_PAGE_SIZE"
          :total="wordsTotal"
          layout="total, prev, pager, next"
          background
          hide-on-single-page
          @current-change="onWordPageChange"
        />
      </div>
    </el-dialog>

    <!-- 编辑单词（单词/释义/音标/例句/时限五字段） -->
    <el-dialog v-model="editDialog" :title="`编辑单词 · ${editForm.spelling}`" width="460px" append-to-body>
      <el-form label-width="72px" label-position="left">
        <el-form-item label="单词" required>
          <el-input v-model="editForm.spelling" maxlength="100" />
        </el-form-item>
        <el-form-item label="释义" required>
          <el-input v-model="editForm.meaning" maxlength="500" />
        </el-form-item>
        <el-form-item label="音标">
          <el-input v-model="editForm.phonetic" placeholder="选填，清空保存即删除" />
        </el-form-item>
        <el-form-item label="例句">
          <el-input v-model="editForm.example" type="textarea" :rows="2" maxlength="500" placeholder="选填，清空保存即删除" />
        </el-form-item>
        <el-form-item label="时限(秒)">
          <el-input-number v-model="editForm.time_limit_s" :min="3" :max="600" controls-position="right" placeholder="自动" style="width: 140px" />
          <el-button v-if="editForm.time_limit_s" link size="small" type="danger" style="margin-left: 8px" @click="editForm.time_limit_s = null">恢复自动</el-button>
        </el-form-item>
      </el-form>
      <p class="word-limit-hint" style="margin: 0 0 4px">时限留空 = 自动公式「5 + 0.75×词长」；该值用作该词打字题基础时长与考核计分权重。</p>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" :loading="editSaving" @click="saveEditWord">保存</el-button>
      </template>
    </el-dialog>

    <!-- 导入三步制 -->
    <el-dialog v-model="importDialog" title="导入词库（上传 → 预览 → 确认）" width="680px" @close="importStep = 'upload'">
      <template v-if="importStep === 'upload'">
        <el-steps :active="0" simple style="margin-bottom: 16px">
          <el-step title="1 上传文件" />
          <el-step title="2 预览确认" />
          <el-step title="3 入库完成" />
        </el-steps>

        <el-alert type="info" :closable="false" style="margin-bottom: 14px">
          <template #title>
            支持 <b>csv / txt / xlsx</b>（≤10MB、≤20000 行），<b>UTF-8 / GBK 自动识别</b>。建议带上表头，可写中文或英文，列顺序随意：
          </template>
          <div style="margin-top: 6px">
            <div>· 必填：<b>单词(拼写/word)</b>、<b>释义(meaning)</b></div>
            <div>· 选填：<b>章节(单元/unit)</b>——预先按教材排好的单元名，导入后<b>单元结构与单词顺序原样保留、不随机打散</b></div>
            <div>· 选填：<b>时限秒(时长/limit_s)</b>——该词打字题基础秒数，留空走默认公式</div>
            <div>· 选填：音标、例句；没有表头时按「拼写,释义,音标,例句」顺序读；含逗号的词请用英文引号包起来</div>
          </div>
        </el-alert>

        <div class="import-example">
          <div class="example-head">
            <span>文件内容示例</span>
            <el-button size="small" type="primary" text @click="downloadTemplate">下载模板 CSV</el-button>
          </div>
          <pre>章节,单词,释义,音标,例句,时限秒
英语单词,chair,椅子,,The chair is comfortable.,3
英语单词,apple,苹果,/ˈæpl/,I eat an apple every day.,3</pre>
        </div>

        <el-radio-group v-model="importMode" style="margin: 14px 0 12px">
          <el-radio value="new">新建词库</el-radio>
          <el-radio value="existing">导入已有词库</el-radio>
        </el-radio-group>
        <el-input v-if="importMode === 'new'" v-model="importBookName" placeholder="新词库名称" style="margin-bottom: 12px" />
        <el-select v-else v-model="importTarget" placeholder="目标词库" style="margin-bottom: 12px; width: 100%">
          <el-option v-for="b in books.filter((x) => canManage(x))" :key="b.id" :label="b.name" :value="b" />
        </el-select>

        <el-tabs>
          <el-tab-pane label="选择文件">
            <el-upload drag :auto-upload="false" :show-file-list="true" :limit="1" accept=".csv,.txt,.xlsx" :on-change="onFileChange" :on-remove="() => (importFile = null)">
              <el-icon style="font-size: 36px; color: var(--wt-text-4)"><UploadFilled /></el-icon>
              <div class="el-upload__text">把文件拖到此处，或 <em>点击选择</em></div>
              <template #tip>
                <div class="el-upload__tip">csv / txt / xlsx，≤10MB，≤20000 行</div>
              </template>
            </el-upload>
          </el-tab-pane>
          <el-tab-pane label="直接粘贴">
            <el-input
              v-model="importPaste"
              type="textarea"
              :rows="6"
              placeholder="每行一个单词，例如：&#10;apple,n. 苹果&#10;banana,n. 香蕉,/bəˈnɑːnə/&#10;（逗号分隔：拼写, 释义, 音标, 例句，后两列可省略）"
            />
          </el-tab-pane>
        </el-tabs>

        <div class="foot-row">
          <el-button type="primary" @click="importPaste.trim() ? uploadPaste() : uploadImport()">解析预览</el-button>
        </div>
      </template>
      <template v-else>
        <el-alert type="info" :closable="false">
          成功 {{ importPreview?.ok_rows }} · 重复 {{ importPreview?.dup_rows }} · 错误 {{ importPreview?.error_rows }}
        </el-alert>
        <el-alert
          v-if="importPreview?.warnings?.length"
          type="warning"
          :closable="false"
          style="margin-top: 10px"
        >
          <template #title>解析体检</template>
          <div v-for="(w, i) in importPreview.warnings" :key="i">· {{ w }}</div>
        </el-alert>
        <h4>重复词处理</h4>
        <el-radio-group v-model="dupStrategy">
          <el-radio value="skip">跳过（默认）</el-radio>
          <el-radio value="overwrite">覆盖（仅更新释义/音标/例句）</el-radio>
        </el-radio-group>
        <div v-if="importPreview?.errors?.length" style="margin-top: 12px">
          <h4>错误行（将跳过并生成报告）</h4>
          <el-table :data="importPreview.errors" size="small" max-height="200">
            <el-table-column prop="line" label="行号" width="80" />
            <el-table-column prop="reason" label="原因" />
          </el-table>
        </div>
        <div v-if="importPreview?.has_unit_column" style="margin-top: 12px">
          <h4>识别到文件里的单元结构（{{ importPreview.units.length }} 个单元，顺序与文件一致）</h4>
          <el-alert type="success" :closable="false" style="margin-bottom: 8px" title="按文件单元导入时，单词归属与顺序完全照搬文件，不会随机打散；同名单元会并入词库里已有的同名单元。" />
          <el-table :data="importPreview.units" size="small" max-height="200">
            <el-table-column prop="name" label="单元名" />
            <el-table-column prop="count" label="词数" width="90" />
          </el-table>
        </div>
        <h4>单元划分</h4>
        <el-radio-group v-model="unitMode">
          <el-radio value="file" :disabled="!importPreview?.has_unit_column">按文件里的单元（保留顺序）</el-radio>
          <el-radio value="none">不划分（全部放默认章节）</el-radio>
          <el-radio value="by-size">按每单元词数</el-radio>
          <el-radio value="by-count">按单元数量</el-radio>
        </el-radio-group>
        <div v-if="unitMode === 'by-size'" style="margin-top: 10px; display: flex; align-items: center; gap: 10px">
          <el-input-number v-model="unitSize" :min="5" :max="200" :step="5" />
          <span>词 / 单元</span>
          <el-button v-for="n in [10, 20, 30, 50, 100]" :key="n" size="small" @click="unitSize = n">{{ n }}</el-button>
        </div>
        <div v-if="unitMode === 'by-count'" style="margin-top: 10px; display: flex; align-items: center; gap: 10px">
          <el-input-number v-model="unitCount" :min="2" :max="500" :step="1" />
          <span>个单元（词数尽量均分）</span>
        </div>
        <el-alert
          v-if="unitMode !== 'none' && unitMode !== 'file'"
          type="info"
          :closable="false"
          title="单词会随机打散后分入 Unit 1、Unit 2 …"
          style="margin-top: 10px; margin-bottom: 12px"
        />
        <div v-if="importPreview?.sample_ok?.length" style="margin: 12px 0">
          <h4>样例（前 20 行）</h4>
          <el-table :data="importPreview.sample_ok" size="small" max-height="220">
            <el-table-column v-if="importPreview.has_unit_column" prop="unit" label="章节" width="120" />
            <el-table-column prop="spelling" label="单词" width="150" />
            <el-table-column prop="meaning" label="释义" />
            <el-table-column v-if="importPreview.has_time_column" label="时限(秒)" width="100">
              <template #default="{ row }">{{ row.time_limit_s ?? '自动' }}</template>
            </el-table-column>
          </el-table>
        </div>
        <div class="foot-row">
          <el-button type="primary" @click="confirmImport">确认入库</el-button>
        </div>
      </template>
    </el-dialog>
  </PageShell>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}
.spacer {
  flex: 1;
}
.book-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}
.book-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.book-head h3 {
  margin: 0;
  font-size: 16px;
}
.pub-ctl {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
}
.pub-txt {
  font-size: 12px;
  color: var(--wt-text-3);
}
.meta {
  color: var(--wt-text-3);
  font-size: 13px;
  margin: 8px 0 12px;
}
.ops {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.word-form {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.word-limit-hint {
  font-size: 12px;
  color: var(--wt-text-4);
  margin: -6px 0 10px;
}
.foot-row {
  margin-top: 16px;
  text-align: right;
}
.import-example {
  border: 1px solid var(--wt-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--wt-card);
}
.example-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
  color: var(--wt-text-2);
  margin-bottom: 6px;
}
.import-example pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--wt-text-2);
  font-family: Consolas, Monaco, monospace;
}
</style>
