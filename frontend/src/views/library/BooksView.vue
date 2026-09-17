<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import PageShell from '../../components/PageShell.vue';
import { booksApi } from '../../api';
import { useAuthStore } from '../../stores/auth';

const router = useRouter();

const auth = useAuthStore();
const tab = ref<'all' | 'mine' | 'public'>('all');
const q = ref('');
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

const newBookName = ref('');
const importDialog = ref(false);
const importStep = ref<'upload' | 'preview'>('upload');
const importFile = ref<File | null>(null);
const importMode = ref<'new' | 'existing'>('new');
const importBookName = ref('');
const importTarget = ref<any>(null);
const importPreview = ref<any>(null);
const dupStrategy = ref<'skip' | 'overwrite'>('skip');
const unitMode = ref<'none' | 'by-size' | 'by-count'>('none');
const unitSize = ref<number>(30); // 每单元词数
const unitCount = ref<number>(10); // 单元数量

const wordForm = ref({ spelling: '', meaning: '', phonetic: '', example: '' });

onMounted(load);

async function load() {
  loading.value = true;
  try {
    const { data } = await booksApi.list(tab.value, q.value);
    books.value = data.items;
  } finally {
    loading.value = false;
  }
}

async function createBook() {
  if (!newBookName.value.trim()) return;
  await booksApi.create(newBookName.value.trim());
  newBookName.value = '';
  ElMessage.success('词库已创建');
  load();
}

async function renameBook(b: any) {
  const { value } = await ElMessageBox.prompt('新名称', '重命名词库', { inputValue: b.name });
  await booksApi.patch(b.id, value);
  load();
}

async function removeBook(b: any) {
  await ElMessageBox.confirm(`将级联软删词库「${b.name}」及其章节与单词（统计保留），确认？`, '删除词库', { type: 'warning' });
  await booksApi.remove(b.id);
  ElMessage.success('已删除');
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
  ElMessage.success('已开始下载');
}

async function clearAllChapters() {
  const b = currentBook.value;
  await ElMessageBox.confirm(
    `将删除词库「${b.name}」的全部章节，单词会保留，之后可用「重新划分单元」再次分组。确认？`,
    '一键清空全部章节',
    { type: 'warning', confirmButtonText: '清空章节', confirmButtonClass: 'el-button--danger' },
  );
  const { data } = await booksApi.clearChapters(b.id);
  ElMessage.success(`已删除 ${data.deleted_chapters} 个章节，${data.kept_words ?? 0} 个单词已保留`);
  openChapters(b);
  load();
}

const resplitDialog = ref(false);
const resplitMode = ref<'by-size' | 'by-count'>('by-size');
const resplitSize = ref(30);
const resplitCount = ref(10);

async function doResplit() {
  const b = currentBook.value;
  const bySize = resplitMode.value === 'by-size' ? resplitSize.value : null;
  const byCount = resplitMode.value === 'by-count' ? resplitCount.value : null;
  const { data } = await booksApi.resplit(b.id, bySize, byCount);
  ElMessage.success(`已随机分成 ${data.chapter_count} 个单元（共 ${data.word_count} 词）`);
  resplitDialog.value = false;
  openChapters(b);
  load();
}

async function openWords(ch: any) {
  currentChapter.value = ch;
  wordsDialog.value = true;
  loadWords();
}

async function loadWords() {
  const { data } = await booksApi.words(currentBook.value.id, {
    chapter_id: currentChapter.value.id,
    q: wordQuery.value || undefined,
  });
  words.value = data.items;
  wordsTotal.value = data.total;
}

async function addWord() {
  if (!wordForm.value.spelling || !wordForm.value.meaning) {
    ElMessage.warning('拼写与释义必填');
    return;
  }
  try {
    await booksApi.createWord({ chapter_id: currentChapter.value.id, ...wordForm.value });
    wordForm.value = { spelling: '', meaning: '', phonetic: '', example: '' };
    loadWords();
  } catch (e: any) {
    if (e.code === 'WORD_DUPLICATE') ElMessage.warning('库内已存在该单词（忽略大小写）');
  }
}

async function removeWord(w: any) {
  await booksApi.deleteWord(w.id);
  loadWords();
}

// ---------- 导入三步制 ----------
function onFileChange(f: any) {
  importFile.value = f.raw || f;
}

async function uploadImport() {
  if (!importFile.value) {
    ElMessage.warning('请选择 csv/txt/xlsx 文件（≤10MB，≤20000 行）');
    return;
  }
  if (importMode.value === 'new' && !importBookName.value.trim()) {
    ElMessage.warning('请输入新词库名称');
    return;
  }
  if (importMode.value === 'existing' && !importTarget.value) {
    ElMessage.warning('请选择目标词库');
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
  importStep.value = 'preview';
}

async function confirmImport() {
  const bySize = unitMode.value === 'by-size' ? unitSize.value : null;
  const byCount = unitMode.value === 'by-count' ? unitCount.value : null;
  const { data } = await booksApi.importConfirm(importPreview.value.preview_token ?? importPreview.value.job_id, dupStrategy.value, bySize, byCount);
  ElMessage.success(
    data.chapter_count
      ? `入库完成：新增 ${data.ok_rows} 词，随机分成 ${data.chapter_count} 个单元`
      : `入库完成：新增 ${data.ok_rows}，覆盖 ${data.overwritten ?? 0}，错误跳过 ${data.error_rows}`,
  );
  importDialog.value = false;
  importStep.value = 'upload';
  load();
}
</script>

<template>
  <PageShell title="词库管理" subtitle="词库 → 章节 → 单词 · 三步导入 · 克隆/导出">
    <div class="toolbar">
      <el-radio-group v-model="tab" @change="load">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="mine">我的</el-radio-button>
        <el-radio-button value="public">公共</el-radio-button>
      </el-radio-group>
      <el-input v-model="q" placeholder="搜索词库" style="width: 220px" clearable @keyup.enter="load" />
      <el-button @click="load">搜索</el-button>
      <div class="spacer"></div>
      <el-input v-model="newBookName" placeholder="新词库名称" style="width: 200px" />
      <el-button type="primary" @click="createBook">新建词库</el-button>
      <el-button type="success" @click="importDialog = true">导入</el-button>
    </div>

    <div class="book-grid" v-loading="loading">
      <el-card v-for="b in books" :key="b.id" shadow="never" class="book-card">
        <div class="book-head">
          <h3>{{ b.name }}</h3>
          <el-tag v-if="b.is_public" type="success" size="small">公共</el-tag>
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

    <!-- 章节管理 -->
    <el-dialog v-model="chaptersDialog" :title="`章节管理 · ${currentBook?.name}`" width="640px">
      <div style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap">
        <el-button size="small" type="primary" @click="addChapter">新增章节</el-button>
        <template v-if="currentBook && canManage(currentBook)">
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
    <el-dialog v-model="wordsDialog" :title="`单词管理 · ${currentChapter?.name}（共 ${wordsTotal}）`" width="760px">
      <el-input v-model="wordQuery" placeholder="搜索拼写/释义" clearable style="margin-bottom: 12px" @input="loadWords" />
      <div class="word-form">
        <el-input v-model="wordForm.spelling" placeholder="单词" style="width: 140px" />
        <el-input v-model="wordForm.meaning" placeholder="释义" style="width: 200px" />
        <el-input v-model="wordForm.phonetic" placeholder="音标（选填）" style="width: 150px" />
        <el-input v-model="wordForm.example" placeholder="例句（选填）" style="width: 180px" />
        <el-button type="primary" @click="addWord">添加</el-button>
      </div>
      <el-table :data="words" size="small" max-height="360">
        <el-table-column prop="spelling" label="单词" width="140" />
        <el-table-column prop="meaning" label="释义" />
        <el-table-column prop="phonetic" label="音标" width="140" />
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" type="danger" plain @click="removeWord(row)">删</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <!-- 导入三步制 -->
    <el-dialog v-model="importDialog" title="导入词库（上传 → 预览 → 确认）" width="640px" @close="importStep = 'upload'">
      <template v-if="importStep === 'upload'">
        <el-radio-group v-model="importMode" style="margin-bottom: 12px">
          <el-radio value="new">新建词库</el-radio>
          <el-radio value="existing">导入已有词库</el-radio>
        </el-radio-group>
        <el-input v-if="importMode === 'new'" v-model="importBookName" placeholder="新词库名称" style="margin-bottom: 12px" />
        <el-select v-else v-model="importTarget" placeholder="目标词库" style="margin-bottom: 12px; width: 100%">
          <el-option v-for="b in books.filter((x) => canManage(x))" :key="b.id" :label="b.name" :value="b" />
        </el-select>
        <input type="file" accept=".csv,.txt,.xlsx" @change="onFileChange" />
        <div class="foot-row">
          <el-button type="primary" @click="uploadImport">解析</el-button>
        </div>
      </template>
      <template v-else>
        <el-alert type="info" :closable="false">
          成功 {{ importPreview?.ok_rows }} · 重复 {{ importPreview?.dup_rows }} · 错误 {{ importPreview?.error_rows }}
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
        <h4>单元划分</h4>
        <el-radio-group v-model="unitMode">
          <el-radio value="none">不划分</el-radio>
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
          v-if="unitMode !== 'none'"
          type="info"
          :closable="false"
          title="单词会随机打散后分入 Unit 1、Unit 2 …"
          style="margin-top: 10px; margin-bottom: 12px"
        />
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
.foot-row {
  margin-top: 16px;
  text-align: right;
}
</style>
