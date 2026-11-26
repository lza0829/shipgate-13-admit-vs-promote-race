import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const d = data.分布 || {}
  return (
    <div className="panel">
      <h2>总览</h2>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>
      <h2>在途发版</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>当前环境</th><th>流水线</th><th>事件序号</th><th>在途锁</th><th>争用戳</th></tr></thead>
          <tbody>
            {(data.在途 || []).map((r) => (
              <tr key={r.编号} className={r.最近争用戳 ? 'row-contention' : ''}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.目标版本}</td>
                <td>{r.状态}</td><td>{r.当前环境 || '-'}</td><td>{r.流水线}</td>
                <td>{r.晋升事件数 ? `${r.最大事件序号}/${r.晋升事件数}` : '-'}</td>
                <td>{r.在途锁阶段 || '-'}</td>
                <td className="stamp-cell" title={r.最近争用戳 || ''}>{r.最近争用戳 || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>在途登记（锁旁读）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>发版单号</th><th>状态</th><th>阶段</th><th>持锁时间</th><th>过期时间</th></tr></thead>
          <tbody>
            {(data.在途登记 || []).map((l) => (
              <tr key={l.编号}><td>{l.发版单号}</td><td>{l.状态}</td><td>{l.阶段}</td><td>{l.持锁时间}</td><td>{l.过期时间}</td></tr>
            ))}
            {(data.在途登记 || []).length === 0 && <tr><td colSpan="5" className="muted">无在途写口（正常：写口为短事务）</td></tr>}
          </tbody>
        </table>
      </div>
      <h2>争用记录（争用戳）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>关联</th><th>动作</th><th>争用戳</th></tr></thead>
          <tbody>
            {(data.争用记录 || []).map((c) => (
              <tr key={c.编号}><td>{c.时间}</td><td>{c.关联编号}</td><td>{c.动作}</td><td>{c.争用戳}</td></tr>
            ))}
            {(data.争用记录 || []).length === 0 && <tr><td colSpan="4" className="muted">无争用</td></tr>}
          </tbody>
        </table>
      </div>
      <h2>各环境版本（名册）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>版本</th><th>更新时间</th></tr></thead>
          <tbody>
            {(data.环境版本 || []).map((v, i) => (
              <tr key={i}><td>{v.应用}</td><td>{v.环境}</td><td>{v.版本}</td><td>{v.更新时间}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
