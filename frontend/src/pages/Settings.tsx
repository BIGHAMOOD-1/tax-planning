import { useEffect, useState } from 'react'
import { api } from '../api'
import { friendlyError } from '../errors'
import { useToast } from '../components/Toast'

interface KeyInfo { source?: string; masked?: string }
interface ProviderPreset { label: string; base_url: string; models: string[]; rerank_models?: string[]; note?: string }

export default function Settings() {
  const [providers, setProviders] = useState<Record<string, ProviderPreset>>({})
  const [embProviders, setEmbProviders] = useState<Record<string, ProviderPreset>>({})
  const [provider, setProvider] = useState('deepseek')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [chatKey, setChatKey] = useState('')
  const [chatKeyInfo, setChatKeyInfo] = useState<KeyInfo>({})
  const [embBase, setEmbBase] = useState('')
  const [embModel, setEmbModel] = useState('')
  const [rerankModel, setRerankModel] = useState('')
  const [embKey, setEmbKey] = useState('')
  const [embKeyInfo, setEmbKeyInfo] = useState<KeyInfo>({})
  const [test, setTest] = useState<Record<string, unknown> | null>(null)
  const [cache, setCache] = useState(false)
  const toast = useToast()

  useEffect(() => {
    api.settings().then((s: any) => {
      setProviders(s.providers || {})
      setEmbProviders(s.embedding_providers || {})
      setProvider(s.chat?.provider || 'deepseek')
      setBaseUrl(s.chat?.base_url || '')
      setModel(s.chat?.model || '')
      setChatKeyInfo(s.chat?.api_key || {})
      setEmbBase(s.embedding?.base_url || '')
      setEmbModel(s.embedding?.model || '')
      setRerankModel(s.embedding?.rerank_model || '')
      setEmbKeyInfo(s.embedding?.api_key || {})
      setCache(!!s.llm?.cache)
    }).catch((e) => toast('error', friendlyError(e)))
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const pickProvider = (p: string) => {
    setProvider(p)
    const preset = providers[p]
    if (preset) {
      setBaseUrl(preset.base_url || '')
      if (preset.models?.[0]) setModel(preset.models[0])
    }
  }

  const save = async () => {
    setTest(null)
    try {
      const r = await api.saveSettings({
        chat: { provider, base_url: baseUrl, model, api_key: chatKey || undefined },
        embedding: { base_url: embBase, model: embModel, rerank_model: rerankModel, api_key: embKey || undefined },
        llm: { cache },
      })
      toast('success', `已保存：对话=${r.chat_model}，向量=${r.embedding_model}。换向量模型后请到「知识库」重建索引。`)
      setChatKey(''); setEmbKey('')
    } catch (e) { toast('error', friendlyError(e)) }
  }

  const runTest = async () => {
    setTest(null)
    try { setTest(await api.testSettings()) } catch (e) { toast('error', friendlyError(e)) }
  }

  const keyHint = (info: KeyInfo) =>
    info.source === 'env' ? `来自环境变量（${info.masked}）——留空即沿用`
      : info.source === 'settings' ? `来自设置（${info.masked}）`
        : '未配置'

  return (
    <div className="container-narrow">
      <div className="topbar"><h2>设置</h2><span className="muted">对话模型 / 向量服务（本地保存，不回显明文）</span></div>

      <div className="card">
        <h3>对话模型</h3>
        <div className="grid">
          <div>
            <label className="field">Provider</label>
            <select value={provider} onChange={(e) => pickProvider(e.target.value)} style={{ width: '100%' }}>
              {Object.entries(providers).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>
          <div>
            <label className="field">模型</label>
            <input value={model} onChange={(e) => setModel(e.target.value)} list="chat-models" />
            <datalist id="chat-models">
              {(providers[provider]?.models || []).map((m) => <option key={m} value={m} />)}
            </datalist>
          </div>
          <div>
            <label className="field">Base URL</label>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </div>
          <div>
            <label className="field">API Key</label>
            <input type="password" value={chatKey} onChange={(e) => setChatKey(e.target.value)}
                   placeholder={keyHint(chatKeyInfo)} />
          </div>
        </div>
        {providers[provider]?.note && <div className="note" style={{ marginTop: 8 }}>{providers[provider].note}</div>}
      </div>

      <div className="card">
        <h3>向量 / 重排（RAG）</h3>
        <div className="grid">
          <div>
            <label className="field">Embedding 模型</label>
            <input value={embModel} onChange={(e) => setEmbModel(e.target.value)} list="emb-models" />
            <datalist id="emb-models">
              {Object.values(embProviders).flatMap((v) => v.models || []).map((m) => <option key={m} value={m} />)}
            </datalist>
          </div>
          <div>
            <label className="field">Rerank 模型</label>
            <input value={rerankModel} onChange={(e) => setRerankModel(e.target.value)} />
          </div>
          <div>
            <label className="field">Base URL</label>
            <input value={embBase} onChange={(e) => setEmbBase(e.target.value)} />
          </div>
          <div>
            <label className="field">API Key</label>
            <input type="password" value={embKey} onChange={(e) => setEmbKey(e.target.value)}
                   placeholder={keyHint(embKeyInfo)} />
          </div>
        </div>
        <div className="note" style={{ marginTop: 8 }}>
          提示：更换 Embedding 模型后，需在「知识库」页重建索引（向量空间变了）。
        </div>
      </div>

      <div className="card">
        <h3>运行</h3>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input type="checkbox" checked={cache} onChange={(e) => setCache(e.target.checked)} />
          LLM 响应缓存（加速重复分析；默认关闭）
        </label>
        <div className="note" style={{ marginTop: 6 }}>
          开启后相同提示词的模型响应会落盘复用；更换模型/提示词时自动失效。
        </div>
      </div>

      <div className="row" style={{ justifyContent: 'flex-end', gap: 10 }}>
        <button className="btn" onClick={runTest}>测试连接</button>
        <button className="btn primary" onClick={save}>保存</button>
      </div>

      {test && (
        <div className="card">
          <h3>连接测试</h3>
          <div className="note">对话：{test.chat ? '✅ 可用' : '❌ 不可用'}</div>
          <div className="note">向量：{test.embedding ? '✅ 可用' : '❌ 不可用'}</div>
          <div className="note">重排：{test.rerank ? '✅ 可用' : '❌ 不可用'}</div>
        </div>
      )}
    </div>
  )
}
