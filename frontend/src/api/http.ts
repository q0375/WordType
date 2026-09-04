import axios, { AxiosError } from 'axios';
import { ElMessage } from 'element-plus';
import { useAuthStore } from '../stores/auth';
import router from '../router';

/** 统一业务错误 */
export class ApiError extends Error {
  code: string;
  detail: any;
  status: number;
  constructor(code: string, message: string, status: number, detail?: any) {
    super(message);
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

const http = axios.create({ baseURL: '/api/v1', timeout: 30000 });

http.interceptors.request.use((config) => {
  const auth = useAuthStore();
  if (auth.token) config.headers.Authorization = `Bearer ${auth.token}`;
  return config;
});

/** 401 处理去重锁：并发多个请求同时 401 时只提示/跳转一次。 */
let handling401 = false;

http.interceptors.response.use(
  (resp) => {
    // D14/K2 滑动续期：原子替换 token
    const newToken = resp.headers['x-new-token'];
    if (newToken) {
      const auth = useAuthStore();
      auth.setToken(newToken);
    }
    return resp;
  },
  (error: AxiosError) => {
    const resp = error.response;
    if (!resp) {
      // 网络错误：交由调用方/离线队列处理
      return Promise.reject(new ApiError('NETWORK_ERROR', '网络异常，请检查连接', 0));
    }
    const data: any = resp.data || {};
    const code = data.code || 'INTERNAL_ERROR';
    if (resp.status === 401) {
      // 认证端点自身的 401（logout 带过期 token 等）静默处理，避免死循环
      const url = resp.config?.url ?? '';
      if (url.includes('/auth/')) {
        return Promise.reject(new ApiError(code, data.message || '未登录', 401, data.detail));
      }
      if (!handling401) {
        handling401 = true;
        if (code === 'AUTH_PWD_CHANGED') ElMessage.error('密码已变更，请重新登录');
        else if (code === 'AUTH_DEACTIVATED') ElMessage.error('账号已注销，30 天内可联系管理员恢复');
        else ElMessage.error('登录已过期，请重新登录');
        const auth = useAuthStore();
        auth.logout();
        router.push('/login');
        setTimeout(() => (handling401 = false), 1500);
      }
    } else if (code !== 'QUOTA_EXCEEDED') {
      // 业务错误 toast；配额类由页面自行展示
      ElMessage.error(data.message || '请求失败');
    }
    return Promise.reject(new ApiError(code, data.message || '请求失败', resp.status, data.detail));
  },
);

export default http;
