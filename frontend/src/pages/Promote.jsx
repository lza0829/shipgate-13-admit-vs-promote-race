import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Promote() {
  const [rows, setRows] = useState([])
  const [rid, setRid] = useState('')
  const [env, setEnv] = useState('预')
  const [err, setErr] = useState('')
  const [stamp, setStamp] = useState(null)
  const [events, setEvents] = useState([])
  const load = async () => {
    const all = await api('/发版单')
    const active = all.filter((r) => ['已入闸', '晋升中'].includes(r.状态))
    setRows(active)
    if (!rid && active[0]) setRid(String(active[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  useEffect(() => {
    if (rid) api(`/发版单/${rid}/晋升事件`).then(setEvents).catch((e) => setErr(e.message))
  }, [rid])
  const go = async () => {
    setErr(''); setStamp(null)
    try {
      await api(`/发版单/${rid}/晋升`, { method: 'POST', body: JSON.stringify({ 目标环境: env }) })
      load()
    } catch (e) {
      setStamp(e.contention || null)
      setErr(e.message)
    }
  }
  return (
    <div className="panel">
      <h2>环境晋升</h2>
      {err && <p className="err">{err}</p>}
      {stamp && (
        <p className="contention">
          争用戳 {stamp.争用戳} · 口径：{stamp.口径} · 结局：{stamp.结局} · 未改环境/未写事件，可重试
        </p>
      )}
      <div className="row">
        <label>发版单
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {rows.map((r) => <option key={r.编号} value={r.编号}>{r.发版单号} · {r.应用} · {r.当前环境}</option>)}
          </select>
        </label>
        <label>目标环境
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="测">测</option>
            <option value="预">预</option>
            <option value="产">产</option>
          </select>
        </label>
        <button type="button" onClick={go}>晋升</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>当前环境</th><th>事件序号</th><th>在途锁</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.最近争用戳 ? 'row-contention' : ''}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.目标版本}</td>
                <td>{r.状态}</td><td>{r.当前环境}</td>
                <td>{r.晋升事件数 ? `${r.最大事件序号}/${r.晋升事件数}` : '-'}</td>
                <td>{r.在途锁阶段 || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>晋升事件序号（本单）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>序号</th><th>来源环境</th><th>目标环境</th><th>裁决</th><th>时间</th><th>备注</th></tr></thead>
          <tbody>
            {events.map((p) => (
              <tr key={p.编号}><td>{p.序号}</td><td>{p.来源环境 || '-'}</td><td>{p.目标环境}</td><td>{p.裁决}</td><td>{p.时间}</td><td>{p.备注}</td></tr>
            ))}
            {events.length === 0 && <tr><td colSpan="6" className="muted">尚无晋升事件</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
