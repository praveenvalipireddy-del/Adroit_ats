package com.mymulya.ats.controller;

import com.mymulya.ats.service.LiveJobScraperService;
import com.mymulya.ats.service.ResumeBotService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.*;

@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "*") // Allows React frontend to connect
public class AtsApiController {

    @Autowired
    private LiveJobScraperService jobScraperService;

    @Autowired
    private ResumeBotService resumeBotService;

    /**
     * 1. Live 24h Job Search Endpoint (Jsoup LinkedIn & Apify Indeed)
     */
    @PostMapping("/jobs/search")
    public ResponseEntity<Map<String, Object>> searchJobs(@RequestBody Map<String, Object> request) {
        String query = (String) request.getOrDefault("query", "Java Developer");
        String location = (String) request.getOrDefault("location", "United States");

        List<Map<String, Object>> jobs = jobScraperService.scrapeLinkedIn24h(query, location, 12);

        Map<String, Object> response = new HashMap<>();
        response.put("query", query);
        response.put("count", jobs.size());
        response.put("contract_only", true);
        response.put("time_filter", "past_24h");
        response.put("results", jobs);

        return ResponseEntity.ok(response);
    }

    /**
     * 2. PDF & Word File Resume Text Extractor
     */
    @PostMapping("/resume-bot/extract")
    public ResponseEntity<Map<String, String>> extractResumeText(@RequestParam("file") MultipartFile file) {
        try {
            String text = resumeBotService.extractText(file.getInputStream(), file.getOriginalFilename());
            return ResponseEntity.ok(Map.of("filename", file.getOriginalFilename(), "text", text));
        } catch (Exception e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        }
    }

    /**
     * 3. AI Master Resume Optimizer Endpoint
     */
    @PostMapping("/resume-bot/optimize")
    public ResponseEntity<Map<String, Object>> optimizeResume(@RequestBody Map<String, String> request) {
        String resumeText = request.getOrDefault("resume_text", "");
        String jdText = request.getOrDefault("jd_text", "");
        String customInstructions = request.getOrDefault("custom_instructions", "");

        if (resumeText.isEmpty() || jdText.isEmpty()) {
            return ResponseEntity.badRequest().body(Map.of("error", "resume_text and jd_text are required"));
        }

        Map<String, Object> result = resumeBotService.optimizeResumeForJd(resumeText, jdText, customInstructions);
        return ResponseEntity.ok(result);
    }

    /**
     * 4. Word (.docx) Document Generator & Direct Download
     */
    @PostMapping("/resume-bot/download-docx")
    public ResponseEntity<byte[]> downloadDocx(@RequestBody Map<String, String> request) {
        try {
            String resumeText = request.getOrDefault("resume_text", "");
            String candidateName = request.getOrDefault("candidate_name", "Consultant");

            byte[] docxBytes = resumeBotService.createDocxResume(resumeText, candidateName);

            String safeFilename = candidateName.replaceAll("[^a-zA-Z0-9_-]", "_") + "_Optimized_Resume.docx";

            return ResponseEntity.ok()
                    .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + safeFilename + "\"")
                    .contentType(MediaType.parseMediaType("application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
                    .body(docxBytes);
        } catch (Exception e) {
            return ResponseEntity.internalServerError().build();
        }
    }
}
