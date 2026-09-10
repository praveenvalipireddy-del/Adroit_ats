# ☕ Java / Spring Boot Native Integration Guide
### For Mymulya Java Engineering Team

If your team prefers **100% Native Java & Spring Boot** (with zero Python), all 2 core features have been implemented natively in Java using standard enterprise libraries (**Jsoup**, **Apache POI**, and **Apache PDFBox**)!

---

## 📁 Ready-to-Drop Java Source Files in This Directory:

1. **`pom_dependencies.xml`**: Maven dependencies (Jsoup, Apache POI, PDFBox).
2. **`LiveJobScraperService.java`**: 100% Pure Java Jsoup scraper for LinkedIn 24h C2C (`f_TPR=r86400&f_JT=C`), Indeed, and Dice.
3. **`ResumeBotService.java`**: Master Prompt ATS keyword matcher and Apache POI Word (`.docx`) file builder.
4. **`AtsApiController.java`**: Spring Boot `@RestController` exposing `/api/jobs/search` and `/api/resume-bot/optimize`.

---

## 🚀 3 Steps to Integrate into Your Spring Boot Microservice:

### Step 1: Add Maven Dependencies to your `pom.xml`
```xml
<!-- Jsoup HTML Parser for 24h Job Scraping -->
<dependency>
    <groupId>org.jsoup</groupId>
    <artifactId>jsoup</artifactId>
    <version>1.17.2</version>
</dependency>

<!-- Apache POI for Word (.docx) Resume Generation -->
<dependency>
    <groupId>org.apache.poi</groupId>
    <artifactId>poi-ooxml</artifactId>
    <version>5.2.5</version>
</dependency>

<!-- Apache PDFBox for PDF Resume Text Extraction -->
<dependency>
    <groupId>org.apache.pdfbox</groupId>
    <artifactId>pdfbox</artifactId>
    <version>3.0.1</version>
</dependency>
```

### Step 2: Copy the 3 Java Files into your Spring Boot `src/main/java/com/mymulya/ats/` package
- Copy `LiveJobScraperService.java` ➔ into your services package.
- Copy `ResumeBotService.java` ➔ into your services package.
- Copy `AtsApiController.java` ➔ into your controllers package.

### Step 3: Run your Spring Boot Application!
Your Spring Boot application will now natively serve:
- `POST /api/jobs/search` (Live 24h Contract Job Scraper)
- `POST /api/resume-bot/optimize` (AI Master Resume Alignment)
- `POST /api/resume-bot/download-docx` (Download Tailored Word `.docx`)
- `POST /api/resume-bot/extract` (PDF / Word text extraction)

---

## 🌟 Benefits of this Java Solution:
- ✅ **100% Java & Spring Boot**: Your existing developers can maintain, debug, and enhance it without needing any Python developer.
- ✅ **No Extra Microservice**: Runs directly inside your existing Spring Boot backend.
- ✅ **Zero Hosting Overhead**: Runs directly on your existing Mymulya server.
