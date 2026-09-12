(function exposeTemporalLawApi() {
    const DEFAULT_API_BASE_URL = 'https://temporal-law-api-867487539733.asia-east1.run.app';
    const API_BASE_URL_KEY = 'temporal-law-api-base-url';
    const ACCESS_CODE_KEY = 'temporal-law-access-code';
    const GEMINI_API_KEY_KEY = 'temporal-law-gemini-api-key';

    const normalizeBaseUrl = value => String(value || DEFAULT_API_BASE_URL).trim().replace(/\/+$/, '');

    function getSettings() {
        return {
            apiBaseUrl: normalizeBaseUrl(localStorage.getItem(API_BASE_URL_KEY) || DEFAULT_API_BASE_URL),
            accessCode: localStorage.getItem(ACCESS_CODE_KEY) || '',
            geminiApiKey: localStorage.getItem(GEMINI_API_KEY_KEY) || ''
        };
    }

    function saveSettings({ apiBaseUrl, accessCode, geminiApiKey }) {
        const normalizedUrl = normalizeBaseUrl(apiBaseUrl);
        localStorage.setItem(API_BASE_URL_KEY, normalizedUrl);
        if (accessCode) localStorage.setItem(ACCESS_CODE_KEY, accessCode.trim());
        else localStorage.removeItem(ACCESS_CODE_KEY);
        if (geminiApiKey) localStorage.setItem(GEMINI_API_KEY_KEY, geminiApiKey.trim());
        else localStorage.removeItem(GEMINI_API_KEY_KEY);
        return getSettings();
    }

    function authHeaders(extra = {}) {
        const settings = getSettings();
        return {
            ...extra,
            // 先送到同源 Flask 代理，再由後端轉送給研究 API，避免瀏覽器 CORS 預檢被擋下。
            ...(settings.apiBaseUrl ? { 'X-Research-Api-Base-Url': settings.apiBaseUrl } : {}),
            ...(settings.accessCode ? { 'X-Access-Code': settings.accessCode } : {}),
            ...(settings.geminiApiKey ? { 'X-Gemini-Api-Key': settings.geminiApiKey } : {})
        };
    }

    async function parseJsonResponse(response, fallbackMessage) {
        const raw = await response.text();
        let data = {};
        try {
            data = raw ? JSON.parse(raw) : {};
        } catch (error) {
            data = {};
        }
        if (!response.ok) {
            const detail = data.error || data.message || raw.replace(/\s+/g, ' ').trim().slice(0, 240);
            throw new Error(detail ? `${fallbackMessage}（HTTP ${response.status}）：${detail}` : `${fallbackMessage}（HTTP ${response.status}）`);
        }
        return data;
    }

    async function getServerConfig() {
        const response = await fetch('/api/research/config');
        return parseJsonResponse(response, '無法讀取伺服器 API 設定。');
    }

    async function uploadDocument({ file, role = 'appeal_petition', caseId = '' }) {
        const settings = getSettings();
        const formData = new FormData();
        formData.append('role', role);
        formData.append('file', file);
        if (caseId) formData.append('case_id', caseId);
        const response = await fetch('/api/research/case-documents', {
            method: 'POST',
            headers: authHeaders(),
            body: formData
        });
        return parseJsonResponse(response, '無法上傳案件文件。');
    }

    async function listCaseDocuments(caseId) {
        const response = await fetch(`/api/research/case-documents?case_id=${encodeURIComponent(caseId)}`, {
            headers: authHeaders()
        });
        return parseJsonResponse(response, '無法讀取案件文件。');
    }

    async function getCaseDocument(documentId) {
        const response = await fetch(`/api/research/case-documents/${encodeURIComponent(documentId)}`, {
            headers: authHeaders()
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.error || '無法讀取案件文件。');
        }
        return {
            blob: await response.blob(),
            contentType: response.headers.get('Content-Type') || ''
        };
    }

    async function listCases(limit = 50) {
        const response = await fetch(`/api/research/cases?limit=${encodeURIComponent(limit)}`, {
            headers: authHeaders()
        });
        return parseJsonResponse(response, '無法讀取案件 Session 清單。');
    }

    async function createCase() {
        const response = await fetch('/api/research/cases', {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({})
        });
        return parseJsonResponse(response, '無法建立新的案件 Session。');
    }

    async function listCaseRuns(caseId, limit = 30) {
        const response = await fetch(
            `/api/research/cases/${encodeURIComponent(caseId)}/runs?limit=${encodeURIComponent(limit)}`,
            { headers: authHeaders() }
        );
        return parseJsonResponse(response, '無法讀取本案件的研究紀錄。');
    }

    async function getAgentRun(runId) {
        const response = await fetch(`/api/research/agent-runs/${encodeURIComponent(runId)}`, {
            headers: authHeaders()
        });
        return parseJsonResponse(response, '無法讀取 Agent 稽核紀錄。');
    }

    async function getArticleHistory(lawId, articleNo) {
        const query = new URLSearchParams({ fname: lawId, article: articleNo });
        const response = await fetch(`/api/research/official-article-history?${query.toString()}`, {
            headers: authHeaders()
        });
        return parseJsonResponse(response, '無法讀取官方法條沿革。');
    }

    async function analyzeCase({ caseId, question, asOf = null, useLocalCache = true, onEvent }) {
        if (!caseId) throw new Error('尚未取得案件 case_id。');
        const settings = getSettings();
        const response = await fetch('/api/research/ask', {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                question,
                as_of: asOf,
                case_id: caseId,
                use_local_cache: useLocalCache
            })
        });

        if (!response.ok || !response.body) {
            const raw = await response.text();
            let data = {};
            try {
                data = raw ? JSON.parse(raw) : {};
            } catch (error) {
                data = {};
            }
            const detail = data.error || data.message || raw.replace(/\s+/g, ' ').trim().slice(0, 240);
            throw new Error(detail ? `無法連線到研究 API（HTTP ${response.status}）：${detail}` : `無法連線到研究 API（HTTP ${response.status}）。`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let completed = null;

        const consumeBlock = block => {
            const normalized = block.replace(/\r/g, '');
            const event = normalized.match(/^event:\s*(.+)$/m)?.[1]?.trim();
            const dataText = normalized
                .split('\n')
                .filter(line => line.startsWith('data:'))
                .map(line => line.slice(5).trim())
                .join('\n');
            if (!event || !dataText) return;
            const data = JSON.parse(dataText);
            if (typeof onEvent === 'function') onEvent(event, data);
            if (event === 'error') throw new Error(data.error || '研究 API 發生錯誤。');
            if (event === 'complete') completed = data;
        };

        while (true) {
            const { value, done } = await reader.read();
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
            buffer = buffer.replace(/\r\n/g, '\n');
            let boundary = buffer.indexOf('\n\n');
            while (boundary >= 0) {
                consumeBlock(buffer.slice(0, boundary));
                buffer = buffer.slice(boundary + 2);
                boundary = buffer.indexOf('\n\n');
            }
            if (done) break;
        }
        if (buffer.trim()) consumeBlock(buffer.trim());
        if (!completed) throw new Error('研究流程意外結束，沒有收到完整結果。');
        return completed;
    }

    window.temporalLawApi = {
        DEFAULT_API_BASE_URL,
        getSettings,
        saveSettings,
        getServerConfig,
        uploadDocument,
        listCaseDocuments,
        getCaseDocument,
        listCases,
        createCase,
        listCaseRuns,
        getAgentRun,
        getArticleHistory,
        analyzeCase
    };
})();
