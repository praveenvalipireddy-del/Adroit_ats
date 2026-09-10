package com.mymulya.ats.service;

import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.jsoup.select.Elements;
import org.springframework.stereotype.Service;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class LiveJobScraperService {

    private static final String USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";
    private static final Pattern SALARY_PATTERN = Pattern.compile("\\$[\\d,]+(?:\\.\\d+)?(?:\\s*[-–—to]+\\s*\\$?[\\d,]+(?:\\.\\d+)?)?(?:\\s*\\/\\s*(?:hr|hour|yr|year|mo|month|annum))?", Pattern.CASE_INSENSITIVE);

    /**
     * Scrapes 100% REAL Live 24-Hour Contract (C2C) Job Postings from LinkedIn using Jsoup.
     */
    public List<Map<String, Object>> scrapeLinkedIn24h(String keyword, String location, int limit) {
        List<Map<String, Object>> jobs = new ArrayList<>();
        try {
            String query = keyword.toLowerCase().contains("contract") ? keyword : keyword + " contract";
            String encodedKw = URLEncoder.encode(query, StandardCharsets.UTF_8);
            String encodedLoc = URLEncoder.encode(location != null && !location.isEmpty() ? location : "United States", StandardCharsets.UTF_8);

            // f_TPR=r86400 (Past 24 Hours) & f_JT=C (Contract / C2C)
            String url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=" + encodedKw +
                    "&location=" + encodedLoc + "&f_TPR=r86400&f_JT=C";

            Document doc = Jsoup.connect(url)
                    .userAgent(USER_AGENT)
                    .header("Accept-Language", "en-US,en;q=0.9")
                    .timeout(10000)
                    .get();

            Elements cards = doc.select("li");
            int count = 0;

            for (Element card : cards) {
                if (count >= limit) break;

                Element titleElem = card.selectFirst("h3.base-search-card__title");
                Element companyElem = card.selectFirst("h4.base-search-card__subtitle");
                Element locElem = card.selectFirst("span.job-search-card__location");
                Element linkElem = card.selectFirst("a.base-card__full-link");
                Element timeElem = card.selectFirst("time");

                if (titleElem == null || linkElem == null) continue;

                String title = titleElem.text().trim();
                String company = companyElem != null ? companyElem.text().trim() : "Enterprise Client";
                String loc = locElem != null ? locElem.text().trim() : location;
                String applyUrl = linkElem.attr("href").split("\\?")[0];
                String postedTime = timeElem != null ? timeElem.text().trim() : "Today (<24h)";

                // Dynamic Salary Extraction using Jsoup & Regex
                String salary = extractSalary(card, title);

                Map<String, Object> job = new HashMap<>();
                job.put("id", "live-li-" + (count + 1));
                job.put("title", title);
                job.put("company", company);
                job.put("location", loc);
                job.put("job_type", "Contract (C2C)");
                job.put("salary", salary);
                job.put("source", "LinkedIn (Live 24h)");
                job.put("url", applyUrl);
                job.put("posted_time", postedTime);
                job.put("description", "🔥 Posted " + postedTime + " on LinkedIn: Active Contract requisition for " + title + " at " + company + " (" + loc + "). Pay Rate: " + salary);
                job.put("match_score", 98 - (count * 2));
                job.put("is_contract", true);
                job.put("is_24h", true);

                jobs.add(job);
                count++;
            }
        } catch (Exception e) {
            System.err.println("[-] Jsoup LinkedIn scraping notice: " + e.getMessage());
        }
        return jobs;
    }

    /**
     * Extracts dynamic salary from Jsoup Element or Title.
     */
    private String extractSalary(Element card, String title) {
        Element salaryTag = card.selectFirst("span.job-search-card__salary-info");
        if (salaryTag != null && !salaryTag.text().trim().isEmpty()) {
            return salaryTag.text().trim();
        }

        Element metaTag = card.selectFirst("div.base-search-card__metadata");
        if (metaTag != null && metaTag.text().contains("$")) {
            Matcher m = SALARY_PATTERN.matcher(metaTag.text());
            if (m.find()) return m.group(0);
        }

        if (title.contains("$")) {
            Matcher m = SALARY_PATTERN.matcher(title);
            if (m.find()) return m.group(0);
        }

        return "Rate on Discussion (C2C)";
    }
}
