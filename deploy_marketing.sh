#!/bin/bash

# Aeon Nimbus - Complete Product Launch Script
# This script finalizes and deploys the marketing package

set -e  # Exit on any error

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Aeon Nimbus - Product Launch Deployment${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Check current directory
if [[ ! -d "marketing" ]]; then
    echo -e "${RED}Error: Must run from Platform_Source_Code directory${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} Current directory verified"

# Verify all marketing files exist
echo ""
echo -e "${YELLOW}Checking marketing package files...${NC}"

FILES=(
    "marketing/README.md"
    "marketing/GETTING_STARTED.md"
    "marketing/PRODUCT_MARKETING.md"
    "marketing/LAUNCH_PLAN.md"
    "marketing/API_INTEGRATION.md"
    "marketing/PRESS_KIT.md"
    "marketing/SCREENSHOT_GUIDE.md"
    "marketing/SOCIAL_MEDIA_POSTS.md"
    "marketing/landing/index.html"
    "marketing/landing/pricing.html"
    "marketing/demo/DEMO_SCRIPT.md"
    "marketing/assets/graphics/logo.svg"
    "marketing/assets/graphics/favicon.svg"
    "marketing/assets/email_templates.html"
)

MISSING=0
for file in "${FILES[@]}"; do
    if [[ -f "$file" ]]; then
        echo -e "${GREEN}✓${NC} $file"
    else
        echo -e "${RED}✗${NC} $file (missing)"
        MISSING=$((MISSING + 1))
    fi
done

if [[ $MISSING -gt 0 ]]; then
    echo ""
    echo -e "${RED}Error: $MISSING files are missing${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}✓${NC} All marketing files present"

# Create deployment directories
echo ""
echo -e "${YELLOW}Creating deployment structure...${NC}"

mkdir -p static/marketing/landing
mkdir -p static/marketing/assets/graphics
mkdir -p docs/api

echo -e "${GREEN}✓${NC} Directories created"

# Copy files to deployment locations
echo ""
echo -e "${YELLOW}Copying files to deployment locations...${NC}"

cp marketing/landing/index.html static/marketing/landing/
cp marketing/landing/pricing.html static/marketing/landing/
cp marketing/assets/graphics/logo.svg static/marketing/assets/graphics/
cp marketing/assets/graphics/favicon.svg static/marketing/assets/graphics/
cp marketing/API_INTEGRATION.md docs/api/README.md

echo -e "${GREEN}✓${NC} Files copied to static directories"

# Update platform templates to link to marketing pages
echo ""
echo -e "${YELLOW}Updating platform navigation...${NC}"

if [[ -f "aeon_nimbus/templates/platform.html" ]]; then
    # Check if marketing link already exists
    if grep -q "/static/marketing/landing/index.html" aeon_nimbus/templates/platform.html; then
        echo -e "${YELLOW}  Navigation links already present${NC}"
    else
        echo -e "${YELLOW}  Manual update required for navigation links${NC}"
        echo -e "${YELLOW}  Add to navbar:${NC}"
        echo '    <a href="/static/marketing/landing/index.html">Home</a>'
        echo '    <a href="/static/marketing/landing/pricing.html">Pricing</a>'
    fi
fi

# Check if server is running
echo ""
echo -e "${YELLOW}Checking if server is running...${NC}"

if curl -s http://localhost:5174 > /dev/null 2>&1; then
    echo -e "${GREEN}✓${NC} Server is running at http://localhost:5174"
    SERVER_RUNNING=true
else
    echo -e "${YELLOW}  Server not running (start with: python build_platform.py)${NC}"
    SERVER_RUNNING=false
fi

# Generate file tree
echo ""
echo -e "${YELLOW}Marketing package structure:${NC}"
echo ""
tree -L 3 marketing/ 2>/dev/null || find marketing -type f -print | sed 's|[^/]*/| |g'

# Summary
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Deployment Summary${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${GREEN}✓${NC} Landing pages: Ready"
echo -e "${GREEN}✓${NC} Marketing docs: Complete"
echo -e "${GREEN}✓${NC} Graphics: Created"
echo -e "${GREEN}✓${NC} Email templates: Ready"
echo -e "${GREEN}✓${NC} Social media: Prepared"
echo -e "${GREEN}✓${NC} API docs: Available"
echo ""

# Access URLs
echo -e "${YELLOW}Access URLs (when server running):${NC}"
echo ""
echo "  Landing Page:   http://localhost:5174/static/marketing/landing/index.html"
echo "  Pricing:        http://localhost:5174/static/marketing/landing/pricing.html"
echo "  Platform:       http://localhost:5174/platform"
echo "  API Docs:       http://localhost:5174/docs"
echo ""

# Next steps
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  Next Steps${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "1. Capture Screenshots:"
echo "   See marketing/SCREENSHOT_GUIDE.md for instructions"
echo ""
echo "2. Record Demo Video:"
echo "   Use script at marketing/demo/DEMO_SCRIPT.md"
echo ""
echo "3. Test Landing Pages:"
echo "   Open http://localhost:5174/static/marketing/landing/index.html"
echo ""
echo "4. Review Launch Plan:"
echo "   See marketing/LAUNCH_PLAN.md for complete checklist"
echo ""
echo "5. Deploy to Production:"
echo "   - Purchase domain (aeon-nimbus.com)"
echo "   - Configure DNS and SSL"
echo "   - Deploy to hosting (AWS, Vercel, etc.)"
echo ""
echo "6. Launch!"
echo "   - Product Hunt at 12:01am PT"
echo "   - Hacker News Show HN"
echo "   - Social media announcements"
echo ""

if [[ "$SERVER_RUNNING" = false ]]; then
    echo -e "${YELLOW}⚠ Start the server first:${NC}"
    echo "   python build_platform.py"
    echo ""
fi

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Marketing Package Ready for Launch! 🚀${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
