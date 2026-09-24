import React, { useState, useEffect } from 'react';
import axios from 'axios';

const Reconciliation = () => {
  const [mode, setMode] = useState('shadow'); // off | shadow | active
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchRuns = async () => {
    try {
      const res = await axios.get('/api/v1/custody/reconciliation/runs');
      setRuns(res.data);
    } catch (err) {
      console.error("Failed to fetch runs", err);
    }
  };

  const startReconciliation = async () => {
    setLoading(true);
    try {
      const payload = {
        source_id: "mock-depo",
        period_from: new Date(Date.now() - 30*24*60*60*1000).toISOString(),
        period_to: new Date().toISOString()
      };
      await axios.post('/api/v1/custody/reconciliation/runs', payload, { params: { mode } });
      await fetchRuns();
    } catch (err) {
      alert("Error starting reconciliation: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchRuns(); }, []);

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold mb-4">Депозитарная сверка</h1>
      
      <div className="mb-4 p-4 bg-gray-100 rounded">
        <label className="block mb-2 font-semibold">Режим работы:</label>
        <select 
          value={mode} 
          onChange={(e) => setMode(e.target.value)}
          className="border p-2 rounded w-full"
        >
          <option value="off">OFF (Выключено)</option>
          <option value="shadow">SHADOW (Тестовый прогон)</option>
          <option value="active">ACTIVE (Боевой режим - требует подтверждения)</option>
        </select>
        <button 
          onClick={startReconciliation} 
          disabled={loading || mode === 'off'}
          className={`mt-4 px-4 py-2 rounded text-white ${mode === 'off' ? 'bg-gray-400' : 'bg-blue-600 hover:bg-blue-700'}`}
        >
          {loading ? 'Запуск...' : 'Запустить сверку'}
        </button>
      </div>

      <div className="mt-8">
        <h2 className="text-xl font-semibold mb-2">История запусков</h2>
        <table className="w-full border-collapse border">
          <thead>
            <tr className="bg-gray-200">
              <th className="border p-2">ID</th>
              <th className="border p-2">Источник</th>
              <th className="border p-2">Период</th>
              <th className="border p-2">Статус</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(run => (
              <tr key={run.id} className="text-center">
                <td className="border p-2">{run.id}</td>
                <td className="border p-2">{run.source_id}</td>
                <td className="border p-2">{new Date(run.period_from).toLocaleDateString()}</td>
                <td className="border p-2"><span className="px-2 py-1 bg-green-100 text-green-800 rounded">{run.status}</span></td>
              </tr>
            ))}
            {runs.length === 0 && <tr><td colSpan="4" className="p-4 text-gray-500">Запусков не найдено</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default Reconciliation;
