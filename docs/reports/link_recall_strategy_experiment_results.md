# Link Recall Strategy Experiment

Run: 20260817T213510Z

## Hit-rate summary

| Strategy | Hits | Total | Items |
|---|---|---|---|
| baseline_grok | 9 | 13 | Sunrise Point, Inspiration Point, Chimney Rock National Monument, Museum of International Folk Art, Queens Garden Trail, Fins and Things Trail, Jud Wiebe Trail, Bear Creek Trail, Piedra Falls |
| site_scoped_grok | 4 | 13 | Sunrise Point, Inspiration Point, The Waterpocket Fold, Chimney Rock National Monument |
| alt_phrasing_grok | 7 | 13 | Sunrise Point, The Waterpocket Fold, Fins and Things Trail, Park Avenue Trail, Jud Wiebe Trail, Bear Creek Trail, Tent Rocks Cave Loop |
| claude_web_search | 0 | 13 | - |
| gemini_google_search | 4 | 13 | Inspiration Point, Museum of International Folk Art, Fins and Things Trail, Tent Rocks Cave Loop |
| openai_search | 1 | 13 | Sunrise Point |

## Per-item detail

### baseline_grok
- **Sunrise Point** [HIT]: `https://www.nps.gov/brca/planyourvisit/sunrise.htm` (class=general, alive=True, accepted=True, query=`site:nps.gov/brca "Sunrise Point" Bryce Canyon National Park attraction landmark`)
- **Inspiration Point** [HIT]: `https://www.nps.gov/brca/planyourvisit/inspiration.htm` (class=general, alive=True, accepted=True, query=`site:nps.gov/brca "Inspiration Point" Bryce Canyon National Park attraction landmark`)
- **The Waterpocket Fold** [no-hit]: `https://www.nps.gov/care/learn/nature/geology.htm` (class=general, alive=True, accepted=False, query=`site:nps.gov/care "The Waterpocket Fold" Capitol Reef National Park attraction landmark`)
- **Chimney Rock National Monument** [HIT]: `https://www.fs.usda.gov/r02/sanjuan/recreation/chimney-rock-national-monument` (class=general, alive=True, accepted=True, query=`"Chimney Rock National Monument" Pagosa Springs, Colorado attraction landmark`)
- **Museum of International Folk Art** [HIT]: `https://www.newmexicoculture.org/museums/museum-of-international-folk-art` (class=general, alive=True, accepted=True, query=`"Museum of International Folk Art" Santa Fe, New Mexico attraction landmark`)
- **Queens Garden Trail** [HIT]: `https://www.nps.gov/brca/planyourvisit/queensgarden.htm` (class=general, alive=True, accepted=True, query=`site:nps.gov/brca "Queens Garden Trail" Bryce Canyon National Park trail hike`)
- **Fins and Things Trail** [HIT]: `https://www.grandcountyutah.net/647/Fins-Things-44-Trail` (class=general, alive=True, accepted=True, query=`"Fins and Things Trail" Moab, Utah trail hike`)
- **Park Avenue Trail**: no candidates (query: `site:nps.gov/arch "Park Avenue Trail" Arches National Park trail hike`)
- **Jud Wiebe Trail** [HIT]: `https://www.fs.usda.gov/r02/gmug/recreation/jud-wiebe-432` (class=general, alive=True, accepted=True, query=`"Jud Wiebe Trail" Telluride, Colorado trail hike`)
- **Bear Creek Trail** [HIT]: `https://www.fs.usda.gov/r02/gmug/recreation/bear-creek-635` (class=general, alive=True, accepted=True, query=`"Bear Creek Trail" Telluride, Colorado trail hike`)
- **Piedra Falls** [HIT]: `https://www.fs.usda.gov/r02/sanjuan/recreation/trails/piedra-falls-trail` (class=general, alive=True, accepted=True, query=`"Piedra Falls" Pagosa Springs, Colorado trail hike`)
- **Tent Rocks Cave Loop** [no-hit]: `https://www.alltrails.com/trail/us/new-mexico/tent-rocks-cave-loop` (class=alltrails, alive=False, accepted=True, query=`"Tent Rocks Cave Loop" Kasha-Katuwe Tent Rocks National Monument, New Mexico trail hike`)
- **Lava Tubes via Lava Flow Trail** [no-hit]: `https://www.alltrails.com/trail/us/utah/lava-tubes-via-lava-flow-trail` (class=alltrails, alive=False, accepted=True, query=`"Lava Tubes via Lava Flow Trail" Snow Canyon State Park, Utah trail hike`)

### site_scoped_grok
- **Sunrise Point** [HIT]: `https://www.nps.gov/brca/planyourvisit/sunrise.htm` (class=general, alive=True, accepted=True, query=`site:nps.gov Sunrise Point Bryce Canyon National Park`)
- **Inspiration Point** [HIT]: `https://www.nps.gov/brca/planyourvisit/inspiration.htm` (class=general, alive=True, accepted=True, query=`site:nps.gov Inspiration Point Bryce Canyon National Park`)
- **The Waterpocket Fold** [HIT]: `https://www.nps.gov/care/planyourvisit/waterpocketdistrict.htm` (class=general, alive=True, accepted=True, query=`site:nps.gov The Waterpocket Fold Capitol Reef National Park`)
- **Chimney Rock National Monument** [HIT]: `https://www.fs.usda.gov/r02/sanjuan/recreation/chimney-rock-national-monument` (class=general, alive=True, accepted=True, query=`site:fs.usda.gov Chimney Rock National Monument Pagosa Springs, Colorado`)
- **Museum of International Folk Art** [no-hit]: `https://www.internationalfolkart.org/exhibitions/` (class=general, alive=False, accepted=True, query=`site:internationalfolkart.org Museum of International Folk Art Santa Fe, New Mexico`)
- **Queens Garden Trail** [no-hit]: `https://www.alltrails.com/trail/us/utah/queens-garden-trail` (class=alltrails, alive=False, accepted=True, query=`site:alltrails.com Queens Garden Trail Bryce Canyon National Park`)
- **Fins and Things Trail** [no-hit]: `https://www.alltrails.com/trail/us/utah/fins-things-trail` (class=alltrails, alive=False, accepted=True, query=`site:alltrails.com Fins and Things Trail Moab, Utah`)
- **Park Avenue Trail**: ERROR ReadTimeout: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out. (read timeout=60) (query: `site:alltrails.com Park Avenue Trail Arches National Park`)
- **Jud Wiebe Trail**: ERROR ReadTimeout: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out. (read timeout=60) (query: `site:alltrails.com Jud Wiebe Trail Telluride, Colorado`)
- **Bear Creek Trail** [no-hit]: `https://www.alltrails.com/trail/us/colorado/bear-creek-falls-via-bear-creek-trail` (class=alltrails, alive=False, accepted=True, query=`site:alltrails.com Bear Creek Trail Telluride, Colorado`)
- **Piedra Falls** [no-hit]: `https://www.alltrails.com/trail/us/colorado/piedra-falls-trail` (class=alltrails, alive=False, accepted=True, query=`site:alltrails.com Piedra Falls Pagosa Springs, Colorado`)
- **Tent Rocks Cave Loop**: ERROR ReadTimeout: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out. (read timeout=60) (query: `site:blm.gov Tent Rocks Cave Loop Kasha-Katuwe Tent Rocks National Monument, New Mexico`)
- **Lava Tubes via Lava Flow Trail** [no-hit]: `https://www.alltrails.com/trail/us/utah/lava-tubes-via-lava-flow-trail` (class=alltrails, alive=False, accepted=True, query=`site:alltrails.com Lava Tubes via Lava Flow Trail Snow Canyon State Park, Utah`)

### alt_phrasing_grok
- **Sunrise Point** [HIT]: `https://www.nps.gov/places/sunrise-point.htm` (class=general, alive=True, accepted=True, query=`Sunrise Point Bryce Canyon National Park official page`)
- **Inspiration Point**: ERROR ReadTimeout: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out. (read timeout=60) (query: `Inspiration Point Bryce Canyon National Park official page`)
- **The Waterpocket Fold** [HIT]: `https://www.utah.com/destinations/national-parks/capitol-reef-national-park/places-to-see/waterpocket-fold/` (class=general, alive=True, accepted=True, query=`The Waterpocket Fold Capitol Reef National Park official page`)
- **Chimney Rock National Monument**: no candidates (query: `Chimney Rock National Monument Pagosa Springs, Colorado official page`)
- **Museum of International Folk Art**: ERROR ReadTimeout: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out. (read timeout=60) (query: `Museum of International Folk Art Santa Fe, New Mexico official page`)
- **Queens Garden Trail**: ERROR ReadTimeout: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out. (read timeout=60) (query: `Queens Garden Trail Bryce Canyon National Park official page`)
- **Fins and Things Trail** [HIT]: `https://www.grandcountyutah.net/647/Fins-Things-44-Trail` (class=general, alive=True, accepted=True, query=`Fins and Things Trail Moab, Utah official page`)
- **Park Avenue Trail** [HIT]: `https://www.nps.gov/places/park-avenue-viewpoint-and-trailhead.htm` (class=general, alive=True, accepted=True, query=`Park Avenue Trail Arches National Park official page`)
- **Jud Wiebe Trail** [HIT]: `https://www.fs.usda.gov/r02/gmug/recreation/jud-wiebe-432` (class=general, alive=True, accepted=True, query=`Jud Wiebe Trail Telluride, Colorado official page`)
- **Bear Creek Trail** [HIT]: `https://www.fs.usda.gov/r02/gmug/recreation/bear-creek-635` (class=general, alive=True, accepted=True, query=`Bear Creek Trail Telluride, Colorado official page`)
- **Piedra Falls**: ERROR ReadTimeout: HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out. (read timeout=60) (query: `Piedra Falls Pagosa Springs, Colorado official page`)
- **Tent Rocks Cave Loop** [HIT]: `https://www.blm.gov/programs/national-conservation-lands/new-mexico/kasha-katuwe-tent-rocks-national-monument` (class=general, alive=True, accepted=True, query=`Tent Rocks Cave Loop Kasha-Katuwe Tent Rocks National Monument, New Mexico official page`)
- **Lava Tubes via Lava Flow Trail** [no-hit]: `https://www.utahsadventurefamily.com/snow-canyon-lava-tubes/` (class=general, alive=False, accepted=True, query=`Lava Tubes via Lava Flow Trail Snow Canyon State Park, Utah official page`)

### claude_web_search
- **Sunrise Point**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `site:nps.gov/brca "Sunrise Point" Bryce Canyon National Park attraction landmark`)
- **Inspiration Point**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `site:nps.gov/brca "Inspiration Point" Bryce Canyon National Park attraction landmark`)
- **The Waterpocket Fold**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `site:nps.gov/care "The Waterpocket Fold" Capitol Reef National Park attraction landmark`)
- **Chimney Rock National Monument**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `"Chimney Rock National Monument" Pagosa Springs, Colorado attraction landmark`)
- **Museum of International Folk Art**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `"Museum of International Folk Art" Santa Fe, New Mexico attraction landmark`)
- **Queens Garden Trail**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `site:nps.gov/brca "Queens Garden Trail" Bryce Canyon National Park trail hike`)
- **Fins and Things Trail**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `"Fins and Things Trail" Moab, Utah trail hike`)
- **Park Avenue Trail**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `site:nps.gov/arch "Park Avenue Trail" Arches National Park trail hike`)
- **Jud Wiebe Trail**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `"Jud Wiebe Trail" Telluride, Colorado trail hike`)
- **Bear Creek Trail**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `"Bear Creek Trail" Telluride, Colorado trail hike`)
- **Piedra Falls**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `"Piedra Falls" Pagosa Springs, Colorado trail hike`)
- **Tent Rocks Cave Loop**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `"Tent Rocks Cave Loop" Kasha-Katuwe Tent Rocks National Monument, New Mexico trail hike`)
- **Lava Tubes via Lava Flow Trail**: ERROR HTTPError: 400 Client Error: Bad Request for url: https://api.anthropic.com/v1/messages (query: `"Lava Tubes via Lava Flow Trail" Snow Canyon State Park, Utah trail hike`)

### gemini_google_search
- **Sunrise Point** [no-hit]: `https://www.nps.gov/places/000/sunrise-point.htm` (class=general, alive=False, accepted=True, query=`site:nps.gov/brca "Sunrise Point" Bryce Canyon National Park attraction landmark`)
- **Inspiration Point** [HIT]: `https://www.nps.gov/places/inspiration-point.htm` (class=general, alive=True, accepted=True, query=`site:nps.gov/brca "Inspiration Point" Bryce Canyon National Park attraction landmark`)
- **The Waterpocket Fold** [no-hit]: `https://www.nps.gov/care/planyourvisit/waterpocket.htm` (class=general, alive=False, accepted=True, query=`site:nps.gov/care "The Waterpocket Fold" Capitol Reef National Park attraction landmark`)
- **Chimney Rock National Monument** [no-hit]: `https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQE78MvOIzDOU50JTlelMNNCU2m6AsPh1VzRJV2G4VnUdnN2UKYFHEvpqC5cOe42l7qPPRbrEEr1BpeSVnvDL2MRjckhJG4rOsZauiCLXsoqwC_zLBfAXI_9U5J9SAc63cuuCF3mdF7RKLk1-f3ngReApFUuRr531v8T7YkmDIwm6Rxz` (class=general, alive=True, accepted=False, query=`"Chimney Rock National Monument" Pagosa Springs, Colorado attraction landmark`)
- **Museum of International Folk Art** [HIT]: `https://www.newmexicoculture.org/museums/museum-of-international-folk-art` (class=general, alive=True, accepted=True, query=`"Museum of International Folk Art" Santa Fe, New Mexico attraction landmark`)
- **Queens Garden Trail** [no-hit]: `https://www.nps.gov/places/000/queens-garden-trail.htm` (class=general, alive=False, accepted=True, query=`site:nps.gov/brca "Queens Garden Trail" Bryce Canyon National Park trail hike`)
- **Fins and Things Trail** [HIT]: `https://www.grandcountyutah.net/197/Fins-Things-44-Trail` (class=general, alive=True, accepted=True, query=`"Fins and Things Trail" Moab, Utah trail hike`)
- **Park Avenue Trail** [no-hit]: `https://www.nps.gov/arch/planyourvisit/courthouse.htm` (class=general, alive=False, accepted=False, query=`site:nps.gov/arch "Park Avenue Trail" Arches National Park trail hike`)
- **Jud Wiebe Trail** [no-hit]: `https://www.visittelluride.com/activity/jud-wiebe-trail/` (class=general, alive=False, accepted=True, query=`"Jud Wiebe Trail" Telluride, Colorado trail hike`)
- **Bear Creek Trail** [no-hit]: `https://www.visittelluride.com/activity/bear-creek-falls/` (class=general, alive=False, accepted=True, query=`"Bear Creek Trail" Telluride, Colorado trail hike`)
- **Piedra Falls** [no-hit]: `https://pagosaspringsareatrails.com/trail/piedra-falls-trail-no-671/` (class=general, alive=None, accepted=True, query=`"Piedra Falls" Pagosa Springs, Colorado trail hike`)
- **Tent Rocks Cave Loop** [HIT]: `https://www.blm.gov/programs/national-conservation-lands/new-mexico/kasha-katuwe-tent-rocks-national-monument` (class=general, alive=True, accepted=True, query=`"Tent Rocks Cave Loop" Kasha-Katuwe Tent Rocks National Monument, New Mexico trail hike`)
- **Lava Tubes via Lava Flow Trail** [no-hit]: `https://www.roadtripryan.com/go/t/utah/st-george/snow-canyon-lava-tubes` (class=general, alive=False, accepted=True, query=`"Lava Tubes via Lava Flow Trail" Snow Canyon State Park, Utah trail hike`)

### openai_search
- **Sunrise Point** [HIT]: `https://www.nps.gov/places/sunrise-point.htm?utm_source=openai` (class=general, alive=True, accepted=True, query=`site:nps.gov/brca "Sunrise Point" Bryce Canyon National Park attraction landmark`)
- **Inspiration Point**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `site:nps.gov/brca "Inspiration Point" Bryce Canyon National Park attraction landmark`)
- **The Waterpocket Fold**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `site:nps.gov/care "The Waterpocket Fold" Capitol Reef National Park attraction landmark`)
- **Chimney Rock National Monument**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `"Chimney Rock National Monument" Pagosa Springs, Colorado attraction landmark`)
- **Museum of International Folk Art**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `"Museum of International Folk Art" Santa Fe, New Mexico attraction landmark`)
- **Queens Garden Trail**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `site:nps.gov/brca "Queens Garden Trail" Bryce Canyon National Park trail hike`)
- **Fins and Things Trail**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `"Fins and Things Trail" Moab, Utah trail hike`)
- **Park Avenue Trail**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `site:nps.gov/arch "Park Avenue Trail" Arches National Park trail hike`)
- **Jud Wiebe Trail**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `"Jud Wiebe Trail" Telluride, Colorado trail hike`)
- **Bear Creek Trail**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `"Bear Creek Trail" Telluride, Colorado trail hike`)
- **Piedra Falls**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `"Piedra Falls" Pagosa Springs, Colorado trail hike`)
- **Tent Rocks Cave Loop**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `"Tent Rocks Cave Loop" Kasha-Katuwe Tent Rocks National Monument, New Mexico trail hike`)
- **Lava Tubes via Lava Flow Trail**: ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://api.openai.com/v1/chat/completions (query: `"Lava Tubes via Lava Flow Trail" Snow Canyon State Park, Utah trail hike`)
