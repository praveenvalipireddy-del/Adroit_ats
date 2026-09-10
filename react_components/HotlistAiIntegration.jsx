import React, { useState } from 'react';

// Configuration: Point to your Python AI Microservice IP / Domain
const AI_SERVICE_BASE_URL = 'http://localhost:5000'; // or 'https://ai.mymulya.com'

/**
 * HotlistAiActions Component
 * Drop this component into each table row of Grand Hotlist / My Hotlist!
 *
 * Props:
 *  - consultant: { id, name, technology, resumeUrl, rate, email }
 */
export const HotlistAiActions = ({ consultant }) => {
  const [showJobsModal, setShowJobsModal] = useState(false);
  const [showResumeModal, setShowResumeModal] = useState(false);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [jobs, setJobs] = useState([]);
  
  const [jdText, setJdText] = useState('');
  const [optimizing, setOptimizing] = useState(false);
  const [optimizationResult, setOptimizationResult] = useState(null);

  // 1. Fetch 24h Live C2C Jobs for this Consultant's Technology
  const handleSearch24hJobs = async () => {
    setShowJobsModal(true);
    setLoadingJobs(true);
    try {
      const res = await fetch(`${AI_SERVICE_BASE_URL}/api/jobs/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: consultant.technology || 'Software Engineer',
          location: 'United States',
          contract_only: true,
          time_filter: 'past_24h',
          source: 'All'
        })
      });
      const data = await res.json();
      setJobs(data.results || []);
    } catch (err) {
      console.error('Failed to fetch live jobs:', err);
    } finally {
      setLoadingJobs(false);
    }
  };

  // 2. Optimize Consultant's Resume against a Target JD
  const handleOptimizeResume = async (targetJd) => {
    setOptimizing(true);
    try {
      const res = await fetch(`${AI_SERVICE_BASE_URL}/api/resume-bot/optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          resume_text: consultant.resumeText || `${consultant.name}\n${consultant.technology}\nSkills: ${consultant.technology}`,
          jd_text: targetJd || jdText,
          candidate_name: consultant.name
        })
      });
      const result = await res.json();
      setOptimizationResult(result);
    } catch (err) {
      console.error('Failed to optimize resume:', err);
    } finally {
      setOptimizing(false);
    }
  };

  // 3. Download Formatted Word (.docx) Resume
  const handleDownloadDocx = async () => {
    if (!optimizationResult?.tailored_resume_text) return;
    try {
      const res = await fetch(`${AI_SERVICE_BASE_URL}/api/resume-bot/download-docx`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          candidate_name: consultant.name,
          resume_text: optimizationResult.tailored_resume_text
        })
      });
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${consultant.name.replace(/\s+/g, '_')}_Optimized_Resume.docx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      console.error('Download failed:', err);
    }
  };

  return (
    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
      {/* Button 1: Live 24h Job Search */}
      <button
        onClick={handleSearch24hJobs}
        style={{
          background: '#0284c7',
          color: '#fff',
          border: 'none',
          padding: '6px 12px',
          borderRadius: '4px',
          cursor: 'pointer',
          fontSize: '12px',
          fontWeight: 600,
          display: 'flex',
          alignItems: 'center',
          gap: '4px'
        }}
      >
        🔍 24h Jobs
      </button>

      {/* Button 2: AI Resume Bot */}
      <button
        onClick={() => setShowResumeModal(true)}
        style={{
          background: '#7c3aed',
          color: '#fff',
          border: 'none',
          padding: '6px 12px',
          borderRadius: '4px',
          cursor: 'pointer',
          fontSize: '12px',
          fontWeight: 600,
          display: 'flex',
          alignItems: 'center',
          gap: '4px'
        }}
      >
        🤖 AI Resume
      </button>

      {/* --- MODAL 1: 24h Live Job Search Results --- */}
      {showJobsModal && (
        <div style={modalOverlayStyle}>
          <div style={modalContentStyle}>
            <div style={modalHeaderStyle}>
              <h3>🔥 Live 24h Contract Jobs for: {consultant.technology} ({consultant.name})</h3>
              <button onClick={() => setShowJobsModal(false)} style={closeBtnStyle}>✕</button>
            </div>
            {loadingJobs ? (
              <p style={{ textAlign: 'center', padding: '20px' }}>⚡ Scraping LinkedIn, Indeed & Dice live...</p>
            ) : jobs.length === 0 ? (
              <p>No new postings found in last 24h.</p>
            ) : (
              <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
                {jobs.map((job) => (
                  <div key={job.id} style={jobCardStyle}>
                    <div>
                      <h4 style={{ margin: '0 0 4px 0' }}>{job.title}</h4>
                      <p style={{ margin: 0, fontSize: '13px', color: '#64748b' }}>
                        🏢 {job.company} • 📍 {job.location} • 💰 {job.salary} • 🕒 {job.posted_time} ({job.source})
                      </p>
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <a href={job.url} target="_blank" rel="noreferrer" style={linkBtnStyle}>Open Post ↗</a>
                      <button
                        onClick={() => {
                          setJdText(job.description || job.title);
                          setShowJobsModal(false);
                          setShowResumeModal(true);
                          handleOptimizeResume(job.description || job.title);
                        }}
                        style={actionBtnStyle}
                      >
                        ⚡ Tailor Resume
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* --- MODAL 2: AI Resume Alignment Studio --- */}
      {showResumeModal && (
        <div style={modalOverlayStyle}>
          <div style={{ ...modalContentStyle, maxWidth: '750px' }}>
            <div style={modalHeaderStyle}>
              <h3>🤖 AI Master Resume Optimizer: {consultant.name}</h3>
              <button onClick={() => setShowResumeModal(false)} style={closeBtnStyle}>✕</button>
            </div>
            <textarea
              placeholder="Paste Job Description (JD) here..."
              value={jdText}
              onChange={(e) => setJdText(e.target.value)}
              style={{ width: '100%', height: '100px', borderRadius: '6px', padding: '10px', border: '1px solid #cbd5e1' }}
            />
            <div style={{ marginTop: '10px' }}>
              <button
                onClick={() => handleOptimizeResume(jdText)}
                disabled={optimizing}
                style={{ ...actionBtnStyle, padding: '8px 16px', fontSize: '14px' }}
              >
                {optimizing ? '⏳ Aligning Master Resume...' : '⚡ Optimize Resume for JD'}
              </button>
            </div>

            {optimizationResult && (
              <div style={{ marginTop: '16px', background: '#f8fafc', padding: '16px', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h4>🎯 ATS Match: {optimizationResult.target_match_percentage}% (Boosted from {optimizationResult.initial_match_percentage}%)</h4>
                  <button onClick={handleDownloadDocx} style={{ ...actionBtnStyle, background: '#16a34a' }}>
                    📥 Download Word (.docx) Resume
                  </button>
                </div>
                <p style={{ fontSize: '13px', color: '#475569' }}>
                  <strong>Aligned Skills:</strong> {optimizationResult.mandatory_skills_aligned?.join(', ')}
                </p>
                <div style={{ maxHeight: '200px', overflowY: 'auto', background: '#fff', padding: '10px', border: '1px solid #cbd5e1', fontSize: '12px', whiteSpace: 'pre-wrap' }}>
                  {optimizationResult.tailored_resume_text}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

// Simple clean modal inline styles
const modalOverlayStyle = { position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 9999 };
const modalContentStyle = { background: '#fff', borderRadius: '8px', width: '90%', maxWidth: '650px', padding: '24px', boxShadow: '0 20px 25px -5px rgba(0,0,0,0.2)' };
const modalHeaderStyle = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' };
const closeBtnStyle = { background: 'none', border: 'none', fontSize: '18px', cursor: 'pointer' };
const jobCardStyle = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px', borderBottom: '1px solid #f1f5f9' };
const linkBtnStyle = { padding: '6px 10px', background: '#f1f5f9', color: '#334155', borderRadius: '4px', textDecoration: 'none', fontSize: '12px', fontWeight: 600 };
const actionBtnStyle = { padding: '6px 10px', background: '#7c3aed', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '12px', fontWeight: 600 };

export default HotlistAiActions;
