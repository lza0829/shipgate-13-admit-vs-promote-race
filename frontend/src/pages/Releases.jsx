import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Releases() {
  const [rows, setRows] = useState([])
  const [apps, setApps] = useState([])
  const [appId, setAppId] = useState('')
  const [ver, setVer] = useState('')
  const [digest, setDigest] = useState('')
  const [err, setErr] = useState('')
  const [stamp, setStamp] = useState(null)
  const load = async () => {
    setRows(await api('/发版单'))
    const a = await api('/应用')
    setApps(a)
    if (!appId && a[0]) setAppId(String(a[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])

  const fail = (e) => {
    setStamp(e.contention || null)
    setErr(e.message)
  }
  const create = async () => {
    setErr(''); setStamp(null)
    try {
      await api('/发版单', {
        method: 'POST',
        body: JSON.stringify({ 应用编号: Number(appId), 目标版本: ver, 制品摘要: digest }),
      })
      setVer(''); setDigest(''); load()
    } catch (e) { fail(e) }
  }
  const pipeline = async (id, status) => {
    setErr(''); setStamp(null)
    try {
      await api(`/发版单/${id}/流水线`, { method: 'POST', body: JSON.stringify({ 流水线: status }) })
      load()
    } catch (e) { fail(e) }
  }
  const admit = async (id) => {
    setErr(''); setStamp(null)
    try {
      await api(`/发版单/${id}/入闸`, { method: 'POST', body: '{}' })
      load()
    } catch (e) { fail(e) }
  }
  return (
    <div className="panel">
      <h2>发版单</h2>
      {err && <p className="err">{err}</p>}
      {stamp && (
        <p className="contention">
          争用戳 {stamp.争用戳} · 口径：{stamp.口径} · 结局：{stamp.结局}
          {stamp.占用阶段 ? ` · 占用阶段：${stamp.占用阶段}` : ''}
        </p>
      )}
      <div className="row">
        <label>应用
          <select value={appId} onChange={(e) => setAppId(e.target.value)}>
            {apps.map((a) => <option key={a.编号} value={a.编号}>{a.名称}</option>)}
          </select>
        </label>
        <label>目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} placeholder="1.4.0" /></label>
        <label>制品摘要<input value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="sha256:..." /></label>
        <button type="button" onClick={create}>申请入闸</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>流水线</th>
              <th>当前环境</th><th>事件序号</th><th>在途锁</th><th>争用戳</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.最近争用戳 ? 'row-contention' : ''}>
                <td>{r.发版单号}</td>
                <td>{r.应用}</td>
                <td>{r.目标版本}</td>
                <td>{r.状态}</td>
                <td>{r.流水线}</td>
                <td>{r.当前环境 || '-'}</td>
                <td>{r.晋升事件数 ? `${r.最大事件序号}/${r.晋升事件数}` : '-'}</td>
                <td>{r.在途锁阶段 || '-'}</td>
                <td className="stamp-cell" title={r.最近争用戳 || ''}>{r.最近争用戳 || '-'}</td>
                <td>
                  <button type="button" className="secondary" onClick={() => pipeline(r.编号, '绿')}>流水线绿</button>{' '}
                  <button type="button" className="danger" onClick={() => pipeline(r.编号, '红')}>流水线红</button>{' '}
                  <button type="button" onClick={() => admit(r.编号)}>入闸</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
