const fs = require('fs');
const path = require('path');

// 1. Configuration
const DOMAIN = 'calaudit.org';
const API_KEY = '43ceee301ad44896bb4325f1242a57f9';

// 2. Locate all sitemaps to extract the URLs dynamically
const sitemapPaths = [
    "/home/rbeade/calaudit-vault/public/sitemap.xml",
"/home/rbeade/calaudit-vault/public/sitemap-metadata.xml",
"/home/rbeade/calaudit-vault/public/sitemap-evidence.xml",
"/home/rbeade/calaudit-vault/public/sitemap-official.xml",
"/home/rbeade/calaudit-vault/public/sitemap-translations.xml"
];

async function pingIndexNow() {
    try {
        console.log('Reading sitemaps to extract URLs...');
        const allUrls = [];

        // Loop through each sitemap path
        for (const sitemapPath of sitemapPaths) {
            if (!fs.existsSync(sitemapPath)) {
                throw new Error(`Sitemap not found at: ${sitemapPath}. Check your build folder path.`);
            }

            console.log(`Extracting from: ${path.basename(sitemapPath)}...`);
            const sitemapContent = fs.readFileSync(sitemapPath, 'utf8');

            // Regex: Matches everything between <loc> and </loc>
            const urlRegex = /<loc>(.*?)<\/loc>/g;
            let match;

            while ((match = urlRegex.exec(sitemapContent)) !== null) {
                allUrls.push(match[1].trim());
            }
        }

        if (allUrls.length === 0) {
            console.log('No URLs found in any of the sitemaps.');
            return;
        }

        console.log(`Found ${allUrls.length} total URLs. Sending to IndexNow...`);

        // Payload required by the IndexNow API
        const data = {
            host: DOMAIN,
            key: API_KEY,
            keyLocation: `https://${DOMAIN}/${API_KEY}.txt`,
            urlList: allUrls
        };

        const response = await fetch('https://api.indexnow.org/indexnow', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json; charset=utf-8'
            },
            body: JSON.stringify(data)
        });

        if (response.ok) {
            console.log('✨ Success! IndexNow submission complete.');
        } else {
            const text = await response.text();
            console.error(`❌ IndexNow failed with status ${response.status}:`, text);
        }

    } catch (error) {
        console.error('❌ IndexNow script error:', error.message);
        process.exit(1);
    }
}

pingIndexNow();
