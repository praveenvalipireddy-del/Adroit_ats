"""Seed data for the institution_aliases table (education-based Sourcing filters).

Each entry: (canonical_id, canonical_name, country, city, [aliases]).
The canonical name is always matched too, so it does not need repeating in the aliases.

Rules for editing this list (it drives which country a LinkedIn education entry is inferred to
be in, so a wrong row silently puts candidates in the wrong filter):
- Only real institutions, with their real city. Nothing guessed.
- Never add an alias that could mean two institutions. Deliberately NOT added: bare "IIT"
  (Indian IITs vs Illinois Institute of Technology), bare "NIT", "USC" (Southern California vs
  South Carolina), "OU", "UB", "TTU", "ASU", "UMD", "NEU", "UCM", "UH", bare "VIT" (Vellore
  vs Pune) etc. The fuzzy matcher + admin review list handles the rest.
- Country is "India" or "USA" - the two values Filters A and B test for.

Bump SEED_VERSION whenever this list changes: startup then inserts the NEW rows only (rows an
admin deleted are not recreated unless their alias is newly added in that version).
"""

SEED_VERSION = 2   # v2: Srinidhi spellings for SNIST

INDIA = "India"
USA = "USA"

INSTITUTIONS = [
    # ---------------- India: IITs ----------------
    ("in-iit-bombay", "Indian Institute of Technology Bombay", INDIA, "Mumbai",
     ["IIT Bombay", "IITB", "IIT-B", "IIT Mumbai"]),
    ("in-iit-delhi", "Indian Institute of Technology Delhi", INDIA, "New Delhi",
     ["IIT Delhi", "IITD", "IIT-D"]),
    ("in-iit-madras", "Indian Institute of Technology Madras", INDIA, "Chennai",
     ["IIT Madras", "IITM", "IIT-M", "IIT Chennai"]),
    ("in-iit-kanpur", "Indian Institute of Technology Kanpur", INDIA, "Kanpur",
     ["IIT Kanpur", "IITK", "IIT-K"]),
    ("in-iit-kharagpur", "Indian Institute of Technology Kharagpur", INDIA, "Kharagpur",
     ["IIT Kharagpur", "IIT KGP", "IIT-KGP"]),
    ("in-iit-roorkee", "Indian Institute of Technology Roorkee", INDIA, "Roorkee",
     ["IIT Roorkee", "IITR", "IIT-R"]),
    ("in-iit-guwahati", "Indian Institute of Technology Guwahati", INDIA, "Guwahati",
     ["IIT Guwahati", "IITG", "IIT-G"]),
    ("in-iit-hyderabad", "Indian Institute of Technology Hyderabad", INDIA, "Sangareddy",
     ["IIT Hyderabad", "IITH", "IIT-H"]),
    ("in-iit-bhu", "Indian Institute of Technology (BHU) Varanasi", INDIA, "Varanasi",
     ["IIT BHU", "IIT (BHU)", "IIT Varanasi", "IIT-BHU"]),

    # ---------------- India: NITs / IIITs ----------------
    ("in-nit-warangal", "National Institute of Technology Warangal", INDIA, "Warangal",
     ["NIT Warangal", "NITW", "NIT-W", "REC Warangal"]),
    ("in-nit-trichy", "National Institute of Technology Tiruchirappalli", INDIA, "Tiruchirappalli",
     ["NIT Trichy", "NIT Tiruchirappalli", "NITT", "REC Trichy"]),
    ("in-nit-surathkal", "National Institute of Technology Karnataka, Surathkal", INDIA, "Surathkal",
     ["NIT Karnataka", "NIT Surathkal", "NITK", "NITK Surathkal"]),
    ("in-nit-calicut", "National Institute of Technology Calicut", INDIA, "Kozhikode",
     ["NIT Calicut", "NITC"]),
    ("in-nit-rourkela", "National Institute of Technology Rourkela", INDIA, "Rourkela",
     ["NIT Rourkela", "NITR"]),
    ("in-iiit-hyderabad", "International Institute of Information Technology, Hyderabad", INDIA, "Hyderabad",
     ["IIIT Hyderabad", "IIIT-H", "IIITH"]),

    # ---------------- India: JNTU family ----------------
    ("in-jntu-hyderabad", "Jawaharlal Nehru Technological University Hyderabad", INDIA, "Hyderabad",
     ["JNTUH", "JNTU Hyderabad", "JNTU-H", "JNTUH College of Engineering Hyderabad"]),
    ("in-jntu-kakinada", "Jawaharlal Nehru Technological University Kakinada", INDIA, "Kakinada",
     ["JNTUK", "JNTU Kakinada", "JNTU-K"]),
    ("in-jntu-anantapur", "Jawaharlal Nehru Technological University Anantapur", INDIA, "Anantapur",
     ["JNTUA", "JNTU Anantapur", "JNTU-A", "JNTU Anantapuramu"]),
    # Many profiles just say "JNTU" with no campus. Country is certain (India), the campus is not -
    # so it gets its own canonical entry instead of being guessed into one of the three above.
    ("in-jntu-unspecified", "Jawaharlal Nehru Technological University (campus not stated)", INDIA, "",
     ["JNTU", "Jawaharlal Nehru Technological University"]),

    # ---------------- India: Telangana / Andhra Pradesh ----------------
    ("in-osmania", "Osmania University", INDIA, "Hyderabad",
     ["Osmania University College of Engineering", "UCE Osmania"]),
    ("in-andhra-university", "Andhra University", INDIA, "Visakhapatnam",
     ["Andhra University College of Engineering", "AU College of Engineering"]),
    ("in-kakatiya", "Kakatiya University", INDIA, "Warangal", []),
    ("in-cbit", "Chaitanya Bharathi Institute of Technology", INDIA, "Hyderabad",
     ["CBIT", "CBIT Hyderabad"]),
    ("in-vnr-vjiet", "VNR Vignana Jyothi Institute of Engineering and Technology", INDIA, "Hyderabad",
     ["VNR VJIET", "VNRVJIET", "VNR Vignana Jyothi"]),
    ("in-vasavi", "Vasavi College of Engineering", INDIA, "Hyderabad", ["VCE Hyderabad"]),
    ("in-griet", "Gokaraju Rangaraju Institute of Engineering and Technology", INDIA, "Hyderabad",
     ["GRIET"]),
    ("in-mgit", "Mahatma Gandhi Institute of Technology", INDIA, "Hyderabad", ["MGIT"]),
    ("in-cvr", "CVR College of Engineering", INDIA, "Hyderabad", []),
    ("in-snist", "Sreenidhi Institute of Science and Technology", INDIA, "Hyderabad",
     ["SNIST", "Srinidhi Institute of Science and Technology", "Sri Nidhi Institute of Science and Technology",
      "Sreenidhi Institute of Science & Technology"]),
    ("in-gitam", "GITAM University", INDIA, "Visakhapatnam",
     ["GITAM", "Gandhi Institute of Technology and Management", "GITAM Deemed to be University"]),
    ("in-kl-university", "Koneru Lakshmaiah Education Foundation", INDIA, "Guntur",
     ["KL University", "KLEF", "K L University", "Koneru Lakshmaiah University"]),
    ("in-acharya-nagarjuna", "Acharya Nagarjuna University", INDIA, "Guntur", ["ANU Guntur"]),
    ("in-sv-university", "Sri Venkateswara University", INDIA, "Tirupati", ["SV University"]),
    ("in-vr-siddhartha", "Velagapudi Ramakrishna Siddhartha Engineering College", INDIA, "Vijayawada",
     ["VR Siddhartha Engineering College", "VRSEC"]),

    # ---------------- India: Tamil Nadu / Karnataka / Kerala ----------------
    ("in-anna-university", "Anna University", INDIA, "Chennai",
     ["Anna University Chennai", "College of Engineering Guindy", "CEG Guindy"]),
    ("in-vit", "Vellore Institute of Technology", INDIA, "Vellore",
     ["VIT University", "VIT Vellore"]),
    ("in-srm", "SRM Institute of Science and Technology", INDIA, "Chennai",
     ["SRM University", "SRMIST", "SRM IST", "SRM University Chennai"]),
    ("in-amrita", "Amrita Vishwa Vidyapeetham", INDIA, "Coimbatore",
     ["Amrita University", "Amrita School of Engineering"]),
    ("in-sastra", "SASTRA Deemed to be University", INDIA, "Thanjavur",
     ["SASTRA University", "SASTRA", "SASTRA Deemed University"]),
    ("in-psg-tech", "PSG College of Technology", INDIA, "Coimbatore", ["PSG Tech"]),
    ("in-sathyabama", "Sathyabama Institute of Science and Technology", INDIA, "Chennai",
     ["Sathyabama University"]),
    ("in-manipal", "Manipal Academy of Higher Education", INDIA, "Manipal",
     ["Manipal University", "Manipal Institute of Technology", "MIT Manipal"]),
    ("in-vtu", "Visvesvaraya Technological University", INDIA, "Belagavi", ["VTU"]),
    ("in-rvce", "RV College of Engineering", INDIA, "Bengaluru", ["RVCE", "R V College of Engineering"]),
    ("in-bmsce", "BMS College of Engineering", INDIA, "Bengaluru", ["BMSCE"]),
    ("in-pes", "PES University", INDIA, "Bengaluru", []),

    # ---------------- India: North / West ----------------
    ("in-bits-pilani", "Birla Institute of Technology and Science, Pilani", INDIA, "Pilani",
     ["BITS Pilani", "BITS"]),
    ("in-thapar", "Thapar Institute of Engineering and Technology", INDIA, "Patiala",
     ["Thapar University"]),
    ("in-dtu", "Delhi Technological University", INDIA, "New Delhi",
     ["DTU", "Delhi College of Engineering"]),
    ("in-pune-university", "Savitribai Phule Pune University", INDIA, "Pune",
     ["University of Pune", "Pune University", "SPPU"]),
    ("in-mumbai-university", "University of Mumbai", INDIA, "Mumbai", ["Mumbai University"]),
    ("in-coep", "College of Engineering Pune", INDIA, "Pune", ["COEP", "COEP Technological University"]),
    ("in-vjti", "Veermata Jijabai Technological Institute", INDIA, "Mumbai", ["VJTI"]),

    # ---------------- USA: Texas ----------------
    ("us-ut-dallas", "The University of Texas at Dallas", USA, "Richardson",
     ["UT Dallas", "UTD", "University of Texas Dallas"]),
    ("us-unt", "University of North Texas", USA, "Denton", ["UNT"]),
    ("us-ut-arlington", "The University of Texas at Arlington", USA, "Arlington",
     ["UT Arlington", "UTA", "University of Texas Arlington"]),
    ("us-utsa", "The University of Texas at San Antonio", USA, "San Antonio", ["UTSA", "UT San Antonio"]),
    ("us-tamu-commerce", "Texas A&M University-Commerce", USA, "Commerce",
     ["Texas A&M Commerce", "TAMUC", "TAMU Commerce", "East Texas A&M University"]),
    ("us-tamu", "Texas A&M University", USA, "College Station", ["TAMU", "Texas A&M"]),
    ("us-tamu-kingsville", "Texas A&M University-Kingsville", USA, "Kingsville",
     ["Texas A&M Kingsville", "TAMUK"]),
    ("us-texas-tech", "Texas Tech University", USA, "Lubbock", []),
    ("us-uh", "University of Houston", USA, "Houston", []),
    ("us-uhcl", "University of Houston-Clear Lake", USA, "Houston", ["UHCL", "UH Clear Lake"]),
    ("us-lamar", "Lamar University", USA, "Beaumont", []),

    # ---------------- USA: Northeast ----------------
    ("us-northeastern", "Northeastern University", USA, "Boston", []),
    ("us-njit", "New Jersey Institute of Technology", USA, "Newark", ["NJIT"]),
    ("us-stevens", "Stevens Institute of Technology", USA, "Hoboken", ["Stevens"]),
    ("us-nyu", "New York University", USA, "New York", ["NYU", "NYU Tandon School of Engineering"]),
    ("us-pace", "Pace University", USA, "New York", []),
    ("us-syracuse", "Syracuse University", USA, "Syracuse", []),
    ("us-rutgers", "Rutgers University", USA, "New Brunswick",
     ["Rutgers, The State University of New Jersey", "Rutgers University-New Brunswick"]),
    ("us-buffalo", "University at Buffalo", USA, "Buffalo",
     ["SUNY Buffalo", "State University of New York at Buffalo"]),
    ("us-stony-brook", "Stony Brook University", USA, "Stony Brook", ["SUNY Stony Brook"]),
    ("us-binghamton", "Binghamton University", USA, "Binghamton", ["SUNY Binghamton"]),
    ("us-bridgeport", "University of Bridgeport", USA, "Bridgeport", []),
    ("us-rivier", "Rivier University", USA, "Nashua", []),
    ("us-harrisburg", "Harrisburg University of Science and Technology", USA, "Harrisburg",
     ["Harrisburg University"]),
    ("us-umbc", "University of Maryland, Baltimore County", USA, "Baltimore", ["UMBC"]),
    ("us-umd", "University of Maryland, College Park", USA, "College Park", ["University of Maryland"]),
    ("us-gmu", "George Mason University", USA, "Fairfax", ["GMU"]),

    # ---------------- USA: Midwest ----------------
    ("us-umkc", "University of Missouri-Kansas City", USA, "Kansas City", ["UMKC"]),
    ("us-ucmo", "University of Central Missouri", USA, "Warrensburg", []),
    ("us-nwmissouri", "Northwest Missouri State University", USA, "Maryville", []),
    ("us-webster", "Webster University", USA, "Webster Groves", []),
    ("us-wright-state", "Wright State University", USA, "Dayton", []),
    ("us-dayton", "University of Dayton", USA, "Dayton", []),
    ("us-cincinnati", "University of Cincinnati", USA, "Cincinnati", []),
    ("us-kent-state", "Kent State University", USA, "Kent", []),
    ("us-cleveland-state", "Cleveland State University", USA, "Cleveland", []),
    ("us-governors-state", "Governors State University", USA, "University Park", []),
    # No bare "IIT" alias: that must stay the Indian IITs' fuzzy territory.
    ("us-illinois-tech", "Illinois Institute of Technology", USA, "Chicago", ["Illinois Tech"]),
    ("us-uic", "University of Illinois Chicago", USA, "Chicago",
     ["UIC", "University of Illinois at Chicago"]),
    ("us-uiuc", "University of Illinois Urbana-Champaign", USA, "Urbana",
     ["UIUC", "University of Illinois at Urbana-Champaign"]),
    ("us-purdue", "Purdue University", USA, "West Lafayette", []),
    ("us-trine", "Trine University", USA, "Angola", []),
    ("us-wichita-state", "Wichita State University", USA, "Wichita", []),
    ("us-oklahoma-state", "Oklahoma State University", USA, "Stillwater", []),

    # ---------------- USA: South / West ----------------
    ("us-clemson", "Clemson University", USA, "Clemson", []),
    ("us-asu", "Arizona State University", USA, "Tempe", []),
    ("us-usc", "University of Southern California", USA, "Los Angeles", ["USC Viterbi"]),
    ("us-sjsu", "San Jose State University", USA, "San Jose", ["SJSU"]),
    ("us-usf", "University of South Florida", USA, "Tampa", ["USF Tampa"]),
    ("us-uf", "University of Florida", USA, "Gainesville", []),
    ("us-georgia-state", "Georgia State University", USA, "Atlanta", []),
    ("us-georgia-tech", "Georgia Institute of Technology", USA, "Atlanta", ["Georgia Tech"]),
    ("us-ncsu", "North Carolina State University", USA, "Raleigh", ["NC State", "NCSU"]),
    ("us-uncc", "University of North Carolina at Charlotte", USA, "Charlotte", ["UNC Charlotte", "UNCC"]),
    ("us-memphis", "University of Memphis", USA, "Memphis", []),
    ("us-cumberlands", "University of the Cumberlands", USA, "Williamsburg", []),
    ("us-campbellsville", "Campbellsville University", USA, "Campbellsville", []),
    ("us-southern-arkansas", "Southern Arkansas University", USA, "Magnolia", []),
    ("us-portland-state", "Portland State University", USA, "Portland", []),
]

# "Any campus" choices for the filters' college box. Not stored in the database: choosing a group
# simply searches all of its member institutions. Members are listed explicitly (never matched by
# name pattern), so e.g. IIIT Hyderabad is NOT part of "IIT (any)".
# (group_id, display_name, country, search_words, [member canonical_ids])
GROUPS = [
    ("group-jntu", "JNTU - any campus", INDIA, ["JNTU", "Jawaharlal Nehru Technological University"],
     ["in-jntu-hyderabad", "in-jntu-kakinada", "in-jntu-anantapur", "in-jntu-unspecified"]),
    ("group-iit", "IIT - any Indian Institute of Technology", INDIA, ["IIT", "Indian Institute of Technology"],
     ["in-iit-bombay", "in-iit-delhi", "in-iit-madras", "in-iit-kanpur", "in-iit-kharagpur", "in-iit-roorkee",
      "in-iit-guwahati", "in-iit-hyderabad", "in-iit-bhu"]),
    ("group-nit", "NIT - any National Institute of Technology", INDIA, ["NIT", "National Institute of Technology"],
     ["in-nit-warangal", "in-nit-trichy", "in-nit-surathkal", "in-nit-calicut", "in-nit-rourkela"]),
    ("group-ut", "University of Texas - any campus", USA, ["UT", "University of Texas"],
     ["us-ut-dallas", "us-ut-arlington", "us-utsa"]),
    ("group-tamu", "Texas A&M - any campus", USA, ["Texas A&M", "TAMU"],
     ["us-tamu", "us-tamu-commerce", "us-tamu-kingsville"]),
    ("group-suny", "SUNY - any campus", USA, ["SUNY", "State University of New York"],
     ["us-buffalo", "us-stony-brook", "us-binghamton"]),
]
