import React, { useState } from "react";
import {
  Box,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Typography,
  Chip,
  CircularProgress,
  IconButton,
  Tooltip,
  Paper
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import CloseIcon from "@mui/icons-material/Close";
import DownloadIcon from "@mui/icons-material/Download";
import LaunchIcon from "@mui/icons-material/Launch";

// Point this to your backend service (Java Spring Boot or Python on port 5000 / your server URL)
const BACKEND_API_BASE_URL = process.env.REACT_APP_AI_SERVICE_URL || "http://localhost:5000";

/**
 * Material-UI Drop-In Component for Dataquad-Outsourcing-UI Hotlist Table
 * Add this to `src/components/Hotlist/hotListColumns.js`!
 */
export const HotlistAiActions = ({ consultant, loading }) => {
  const [openJobsModal, setOpenJobsModal] = useState(false);
  const [openResumeModal, setOpenResumeModal] = useState(false);

  const [loadingJobs, setLoadingJobs] = useState(false);
  const [jobs, setJobs] = useState([]);

  const [jdText, setJdText] = useState("");
  const [optimizing, setOptimizing] = useState(false);
  const [optimizationResult, setOptimizationResult] = useState(null);

  if (loading) return null;

  // 1. Fetch 24h Live Contract (C2C) Jobs for Consultant's Technology
  const handleFetch24hJobs = async () => {
    setOpenJobsModal(true);
    setLoadingJobs(true);
    try {
      const res = await fetch(`${BACKEND_API_BASE_URL}/api/jobs/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: consultant?.technology || "Software Engineer",
          location: consultant?.location || "United States",
          contract_only: true,
          time_filter: "past_24h"
        })
      });
      const data = await res.json();
      setJobs(data.results || []);
    } catch (err) {
      console.error("Failed to fetch live 24h jobs:", err);
    } finally {
      setLoadingJobs(false);
    }
  };

  // 2. Optimize Consultant's Resume against Target JD
  const handleOptimizeResume = async (targetJd) => {
    setOptimizing(true);
    try {
      const res = await fetch(`${BACKEND_API_BASE_URL}/api/resume-bot/optimize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resume_text: consultant?.resumeText || `${consultant?.name || 'Consultant'}\n${consultant?.technology || 'Software Engineer'}\nSkills: ${consultant?.technology}`,
          jd_text: targetJd || jdText,
          candidate_name: consultant?.name || "Consultant"
        })
      });
      const result = await res.json();
      setOptimizationResult(result);
    } catch (err) {
      console.error("Resume optimization error:", err);
    } finally {
      setOptimizing(false);
    }
  };

  // 3. Download Formatted Word (.docx) Resume
  const handleDownloadDocx = async () => {
    if (!optimizationResult?.tailored_resume_text) return;
    try {
      const res = await fetch(`${BACKEND_API_BASE_URL}/api/resume-bot/download-docx`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          candidate_name: consultant?.name || "Consultant",
          resume_text: optimizationResult.tailored_resume_text
        })
      });
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${(consultant?.name || "Consultant").replace(/\s+/g, "_")}_Optimized_Resume.docx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      console.error("Docx download failed:", err);
    }
  };

  return (
    <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
      {/* 🔍 Find 24h Jobs Button */}
      <Tooltip title={`Find 24h live C2C jobs for ${consultant?.technology || "technology"}`}>
        <Button
          variant="outlined"
          size="small"
          startIcon={<SearchIcon fontSize="small" />}
          onClick={handleFetch24hJobs}
          sx={{
            textTransform: "none",
            fontSize: "0.75rem",
            py: 0.5,
            px: 1,
            borderColor: "primary.main",
            color: "primary.main",
            fontWeight: 600,
            whiteSpace: "nowrap"
          }}
        >
          24h Jobs
        </Button>
      </Tooltip>

      {/* 🤖 AI Resume Bot Button */}
      <Tooltip title={`AI Tailor resume for ${consultant?.name || "consultant"}`}>
        <Button
          variant="contained"
          size="small"
          startIcon={<AutoAwesomeIcon fontSize="small" />}
          onClick={() => setOpenResumeModal(true)}
          sx={{
            textTransform: "none",
            fontSize: "0.75rem",
            py: 0.5,
            px: 1,
            background: "linear-gradient(135deg, #7c3aed, #6366f1)",
            color: "#fff",
            fontWeight: 600,
            whiteSpace: "nowrap",
            "&:hover": {
              background: "linear-gradient(135deg, #6d28d9, #4f46e5)"
            }
          }}
        >
          AI Resume
        </Button>
      </Tooltip>

      {/* ----------------- MODAL 1: 24H LIVE JOBS MODAL ----------------- */}
      <Dialog
        open={openJobsModal}
        onClose={() => setOpenJobsModal(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ m: 0, p: 2, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            🔥 Live 24h Contract Jobs for: {consultant?.technology} ({consultant?.name})
          </Typography>
          <IconButton onClick={() => setOpenJobsModal(false)} size="small">
            <CloseIcon />
          </IconButton>
        </DialogTitle>

        <DialogContent dividers sx={{ p: 2 }}>
          {loadingJobs ? (
            <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", py: 4 }}>
              <CircularProgress size={36} sx={{ mb: 2 }} />
              <Typography variant="body2" color="text.secondary">
                Scraping live contract requisitions from LinkedIn & Indeed...
              </Typography>
            </Box>
          ) : jobs.length === 0 ? (
            <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 3 }}>
              No new contract postings found in the past 24 hours.
            </Typography>
          ) : (
            <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
              {jobs.map((job) => (
                <Paper
                  key={job.id}
                  variant="outlined"
                  sx={{
                    p: 2,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 2,
                    "&:hover": { borderColor: "primary.main" }
                  }}
                >
                  <Box>
                    <Box sx={{ display: "flex", gap: 1, mb: 0.5, alignItems: "center" }}>
                      <Chip label={`🔥 ${job.posted_time || "Today (<24h)"}`} size="small" color="error" variant="outlined" sx={{ fontWeight: 700, height: 20, fontSize: "0.68rem" }} />
                      <Chip label={job.source} size="small" color="success" variant="outlined" sx={{ fontWeight: 600, height: 20, fontSize: "0.68rem" }} />
                    </Box>
                    <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                      {job.title}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      🏢 {job.company} • 📍 {job.location} • 💰 {job.salary || "Rate on Discussion (C2C)"}
                    </Typography>
                  </Box>

                  <Box sx={{ display: "flex", gap: 1, flexShrink: 0 }}>
                    <Button
                      variant="outlined"
                      size="small"
                      href={job.url}
                      target="_blank"
                      endIcon={<LaunchIcon fontSize="small" />}
                      sx={{ textTransform: "none", fontSize: "0.75rem" }}
                    >
                      Apply ↗
                    </Button>
                    <Button
                      variant="contained"
                      size="small"
                      startIcon={<AutoAwesomeIcon fontSize="small" />}
                      onClick={() => {
                        setJdText(job.description || job.title);
                        setOpenJobsModal(false);
                        setOpenResumeModal(true);
                        handleOptimizeResume(job.description || job.title);
                      }}
                      sx={{
                        textTransform: "none",
                        fontSize: "0.75rem",
                        background: "linear-gradient(135deg, #7c3aed, #6366f1)"
                      }}
                    >
                      ⚡ Tailor Resume
                    </Button>
                  </Box>
                </Paper>
              ))}
            </Box>
          )}
        </DialogContent>
      </Dialog>

      {/* ----------------- MODAL 2: AI RESUME BOT MODAL ----------------- */}
      <Dialog
        open={openResumeModal}
        onClose={() => setOpenResumeModal(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ m: 0, p: 2, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            🤖 AI Master Resume Optimizer: {consultant?.name}
          </Typography>
          <IconButton onClick={() => setOpenResumeModal(false)} size="small">
            <CloseIcon />
          </IconButton>
        </DialogTitle>

        <DialogContent dividers sx={{ p: 2 }}>
          <TextField
            label="Target Client Job Description (JD)"
            multiline
            rows={4}
            fullWidth
            placeholder="Paste client job description requirements here..."
            value={jdText}
            onChange={(e) => setJdText(e.target.value)}
            sx={{ mb: 2 }}
          />

          <Button
            variant="contained"
            fullWidth
            onClick={() => handleOptimizeResume(jdText)}
            disabled={optimizing || !jdText.trim()}
            startIcon={optimizing ? <CircularProgress size={18} color="inherit" /> : <AutoAwesomeIcon />}
            sx={{
              py: 1.2,
              fontWeight: 700,
              background: "linear-gradient(135deg, #7c3aed, #6366f1)"
            }}
          >
            {optimizing ? "Aligning Master Resume against JD..." : "⚡ Run AI Master Optimization"}
          </Button>

          {optimizationResult && (
            <Paper variant="outlined" sx={{ mt: 2, p: 2, background: "#f8fafc" }}>
              <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1.5 }}>
                <Typography variant="subtitle1" sx={{ fontWeight: 700, color: "success.main" }}>
                  🎯 ATS Match Score: {optimizationResult.target_match_percentage}% (Boosted from {optimizationResult.initial_match_percentage}%)
                </Typography>
                <Button
                  variant="contained"
                  color="success"
                  size="small"
                  startIcon={<DownloadIcon />}
                  onClick={handleDownloadDocx}
                  sx={{ textTransform: "none", fontWeight: 700 }}
                >
                  📥 Download Word (.docx)
                </Button>
              </Box>

              <Typography variant="caption" sx={{ display: "block", mb: 1, color: "text.secondary" }}>
                <strong>Aligned Mandatory Skills:</strong> {optimizationResult.mandatory_skills_aligned?.join(", ")}
              </Typography>

              <TextField
                label="Tailored Resume Output (Editable)"
                multiline
                rows={8}
                fullWidth
                value={optimizationResult.tailored_resume_text || ""}
                onChange={(e) => setOptimizationResult({ ...optimizationResult, tailored_resume_text: e.target.value })}
                sx={{ background: "#fff" }}
              />
            </Paper>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  );
};

export default HotlistAiActions;
