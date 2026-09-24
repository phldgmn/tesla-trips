# **Technical Guide: Ingesting and Maintaining a Local European Tesla Supercharger Database for Trip Planning**

This report presents a technical methodology for aggregating, building, and maintaining a local, offline-capable database of European Tesla Supercharger locations tailored specifically for Tesla drivers. It focuses exclusively on charger-relevant metrics: geospatial coordinates, physical stall counts, peak power capability, operational status, and pricing. Non-charger attributes (such as parking geometries or street access details) and non-Tesla charging networks are excluded.

## **Technical Data Sources for Tesla Superchargers**

Gathering accurate Supercharger data requires leveraging both official Tesla web services and dedicated community tracking databases.

### **1\. Supercharge.info REST API**

Supercharge.info serves as a primary source for tracking the global Supercharger network1. Its backend exposes structured REST endpoints returning JSON payloads without requiring API key authorization or subscription fees1.

> * **Primary Endpoint:** <https://supercharge.info/service/supercharge/allSites>  
>   \[cite: 1, 3\]  
> * **Retrieved Attributes:**  
>   * **Geospatial Coordinates:** Explicit latitude and longitude fields nested within the gps object4.  
>   * **Hardware Capabilities:** Total physical stall count (stalls) and peak power rating in kW (power)6.  
>   * **Operational Status:** Site lifecycle stages including PERMIT, CONSTRUCTION, OPEN, EXPANDING, and TEMP\_CLOSED8.  
> * **Delta Tracking Endpoints:**  
>   * supercharge/databaseInfo: Yields a single timestamp signature indicating when the database was last updated9.  
>   * supercharge/allChanges: Provides log entries for status transitions (e.g., sites moving from construction to open)1.

### **2\. Tesla Guest Charging GraphQL API**

Tesla operates an unauthenticated GraphQL service used to populate public interactive maps on its website. This gateway allows direct retrieval of site status and Supercharger pricing.

> * **Endpoint:** <https://www.tesla.com/charging/guest/api/graphql> executing the getGuestChargingSiteDetails operation8.  
> * **Request Structure:** HTTP POST request carrying JSON web headers.  
> * **Retrieved Attributes:**  
>   * **Geospatial Coordinates:** Centroid latitude and longitude coordinates.  
>   * **Operational Status:** Real-time closure indicator (site\_closed: true/false).  
>   * **Capacity:** Total physical stalls and active operational stalls.  
>   * **Pricing Data:** Applicable Supercharger energy tariffs (€/kWh) and time-of-use pricing bands.

### **3\. Tesla Mobile App GraphQL Endpoint**

Tesla’s mobile application infrastructure utilizes a GraphQL endpoint to serve nearby charging station data based on geographic bounding boxes.

> * **Endpoint:** akamai-apigateway-charging-ownership.tesla.com/graphql running the GetNearbyChargingSites operation10.  
> * **Request Structure:** Authenticated HTTP POST query accepting user coordinates and bounding box coordinates (northwestCorner and southeastCorner)10.  
> * **Retrieved Attributes:** Site centroid coordinates, total stall count, available stall count, peak power rating in kW (maxPowerKw), and active outages10.

### **4\. Open-Source Charging Repositories (Filtered for Tesla)**

Open-source registries provide complementary validation for Supercharger coordinates and power specs:

> * **Open Charge Map API (api.openchargemap.io/v3/data):** Querying with parameter operatorId=23 filters results strictly to Tesla Superchargers11. It returns latitude, longitude, status codes (50=Operational, 100=Not Operational, 150=Planned), total connectors, and peak power (PowerKW).  
> * **OpenStreetMap via Overpass API:** Querying Overpass QL specifically for amenity=charging\_station combined with brand=Tesla Supercharger extracts latitude, longitude, capacity=\*, and maximum power ratings while filtering out non-charging physical geometry tags4.

## **Data Source Comparison for Tesla Drivers**

| Data Source | Primary Endpoint / Format | Lat/Long Coordinates | Total Stalls & Max Power (kW) | Operational Status Granularity | Pricing Data (€/kWh) |
| :---- | :---- | :---- | :---- | :---- | :---- |
| **Supercharge.info** | supercharge/allSites (JSON)1 | Explicit Lat/Long4 | Explicit stall count & peak kW6 | Detailed: Permit, Construction, Open, Temp Closed8 | Not included13 |
| **Tesla Guest GraphQL** | tesla.com/charging/guest/api/graphql (JSON) | Explicit Lat/Long | Stall count included | Binary: site\_closed: true/false \[cite: \] | Direct live tariffs (€/kWh) |
| **Tesla Mobile App GraphQL** | akamai-apigateway.../graphql (JSON)10 | Centroid Lat/Long10 | Total stalls & max kW output10 | Active outages & site status10 | Dynamic pricing payload fragment10 |
| **Open Charge Map** | api.openchargemap.io/v3/data (operatorId=23)11 | Explicit Lat/Long | Connector count & peak kW | Status codes: 50 (Open), 100 (Closed), 150 (Planned) | Rare / Variable |

## **Harmonized Local Database Schema**

To consolidate these sources into a single offline database (such as SQLite or SpatiaLite), use the following charger-focused relational schema:

| Field Name | SQL Datatype | Constraints | Primary Source | Functional Description |
| :---- | :---- | :---- | :---- | :---- |
| site\_id | INTEGER | Primary Key (Auto) | Local System Generator | Internal unique identifier. |
| supercharge\_info\_id | INTEGER | Unique Index | Supercharge.info (id)1 | Cross-reference identifier for tracking status changes9. |
| tesla\_location\_id | VARCHAR(64) | Indexed | Tesla Guest API (id) | Official Tesla internal GraphQL location key. |
| site\_name | VARCHAR(128) | Not Null | Supercharge.info (name)5 | Human-readable name (e.g., "Nebikon, Switzerland")8. |
| latitude | DOUBLE | Not Null | Supercharge.info / Tesla Guest API4 | WGS84 Geographic Latitude coordinate. |
| longitude | DOUBLE | Not Null | Supercharge.info / Tesla Guest API4 | WGS84 Geographic Longitude coordinate. |
| country\_code | VARCHAR(2) | Not Null | Supercharge.info (country)9 | ISO 3166-1 alpha-2 European country code9. |
| total\_stalls | INTEGER | Default: 0 | Supercharge.info / Tesla Guest API | Total physical Supercharger stalls installed. |
| max\_power\_kw | INTEGER | Default: 0 | Supercharge.info (power)6 | Peak charging rate in kW (e.g., 150, 250, 350\)6. |
| status | VARCHAR(32) | Not Null | Supercharge.info (status) | Lifecycle status (OPEN, CONSTRUCTION, PLANNED, TEMP\_CLOSED)8. |
| pricing\_kwh | DECIMAL(5,2) | Default: Null | Tesla Guest GraphQL API | Current energy rate per kWh in local currency (€/kWh). |
| last\_updated\_utc | TIMESTAMP | Current Stamp | Ingestion Pipeline | UTC timestamp of last database modification. |

## **Single Implementation Flow to Gather the Data**

Building the local database follows a single sequential pipeline:

\[Step 1: Ingest Baseline Metadata\] \---\> \[Step 2: Conflate & Deduplicate Locations\] \---\> \[Step 3: Fetch Pricing & Live Status\] \---\> \[Step 4: Load into Local Database\]

### **Step 1: Ingest Baseline Metadata**

Download the full global dataset from Supercharge.info by issuing an HTTP GET request to <https://supercharge.info/service/supercharge/allSites1>. Filter the incoming JSON array to retain only records where the address.region is "Europe" or the address.country matches European ISO codes9. Extract site IDs, site names, GPS coordinates, total stalls, maximum power ratings (kW), and operational statuses4.

### **Step 2: Conflate and Deduplicate Locations**

If supplementing with data from Open Charge Map (operatorId=23) or OpenStreetMap, perform deduplication using a spatial proximity check4:

> 1. Index existing coordinates using a Haversine radial metric distance formula.  
> 2. For any incoming site within a 200-meter radius of an existing location, merge the attributes rather than creating a duplicate entry4.  
> 3. Prioritize Supercharge.info for site lifecycle states (CONSTRUCTION, OPEN, EXPANDING, TEMP\_CLOSED)8.

### **Step 3: Fetch Pricing and Live Status Data**

Execute an HTTP POST query against Tesla's Guest GraphQL endpoint (<https://www.tesla.com/charging/guest/api/graphql> executing getGuestChargingSiteDetails) for the target European coordinate bounding box8. Extract active site pricing (€/kWh) and verify whether site\_closed is false.

### **Step 4: Load into Local Database**

Write the merged data into your local SQLite database structured according to the relational schema defined above. Create a spatial index (or standard composite B-Tree index on latitude and longitude) to enable rapid offline proximity lookups during trip planning.

## **Maintenance and Update Strategy**

To keep your local database accurate for personal trip planning without generating excessive web traffic, implement a tiered interval-based update routine:

### **Daily Update Routine (Status Delta Check)**

> * **Action:** Query the Supercharge.info timestamp endpoint at <https://supercharge.info/service/supercharge/databaseInfo9>.  
> * **Logic:** Compare the returned timestamp signature against your local last\_updated\_utc marker9. If the remote timestamp is newer, fetch <https://supercharge.info/service/supercharge/allChanges> to identify changed site IDs1.  
> * **Impact:** Ingests recent status updates (such as newly opened locations or temporary site closures) without re-downloading the entire global dataset1.

### **Bi-Weekly Update Routine (Pricing Refresh)**

> * **Action:** Query Tesla's Guest GraphQL API (getGuestChargingSiteDetails) across your indexed European site coordinates8.  
> * **Logic:** Update the pricing\_kwh and status fields in your local database.  
> * **Impact:** Accounts for regional changes in energy tariffs (€/kWh) across European countries.

### **Monthly Update Routine (Full Database Re-sync)**

> * **Action:** Re-run the full ingestion script from <https://supercharge.info/service/supercharge/allSites1>.  
> * **Logic:** Perform a complete database purge and rebuild, re-applying spatial deduplication against Open Charge Map11.  
> * **Impact:** Captures newly planned Supercharger sites (PERMIT status), hardware expansions (such as additional V3/V4 stalls added to existing locations), and updated maximum kW ratings6.

#### **Works cited**

> 1. Supercharger Admin, [https://supercharge.info/admin/](https://supercharge.info/admin/)  
> 2. Downloading supercharger data \- Site Feedback, [https://forum.supercharge.info/t/downloading-supercharger-data/215](https://forum.supercharge.info/t/downloading-supercharger-data/215)  
> 3. API endpoint for all data of a particular site? \- forum.supercharge.info, [https://forum.supercharge.info/t/api-endpoint-for-all-data-of-a-particular-site/1433](https://forum.supercharge.info/t/api-endpoint-for-all-data-of-a-particular-site/1433)  
> 4. Bulk import of Tesla Superchargers in the United States \- OpenStreetMap Community Forum, [https://community.openstreetmap.org/t/bulk-import-of-tesla-superchargers-in-the-united-states/131561](https://community.openstreetmap.org/t/bulk-import-of-tesla-superchargers-in-the-united-states/131561)  
> 5. Split out coordinates to Longitude, Latitude \- Site Feedback \- forum.supercharge.info, [https://forum.supercharge.info/t/split-out-coordinates-to-longitude-latitude/1675](https://forum.supercharge.info/t/split-out-coordinates-to-longitude-latitude/1675)  
> 6. Nearby Chargers · Issue \#612 · timdorr/tesla-api \- GitHub, [https://github.com/timdorr/tesla-api/issues/612](https://github.com/timdorr/tesla-api/issues/612)  
> 7. supercharge.info, [https://supercharge.info/map?Center=48.056134,12.708525\&Zoom=6\&RangeMi=175](https://supercharge.info/map?Center=48.056134,12.708525&Zoom=6&RangeMi=175)  
> 8. Neue Tesla Fahrzeug API \- Seite 33 \- TFF Forum, [https://tff-forum.de/t/neue-tesla-fahrzeug-api/307668?page=33](https://tff-forum.de/t/neue-tesla-fahrzeug-api/307668?page=33)  
> 9. Accessing the Tesla Fleet API \- CData Software, [https://www.cdata.com/kb/articles/tesla-api.rst](https://www.cdata.com/kb/articles/tesla-api.rst)  
> 10. Charging Endpoints | Tesla Fleet API, [https://developer.tesla.com/docs/fleet-api/endpoints/charging-endpoints](https://developer.tesla.com/docs/fleet-api/endpoints/charging-endpoints)  
> 11. Open Charge Map EV Station Search \- Apify, [https://apify.com/ryanclinton/open-charge-map](https://apify.com/ryanclinton/open-charge-map)  
> 12. OpenStreetMap \- Code & Technical Questions \- forum.supercharge.info, [https://forum.supercharge.info/t/openstreetmap/3281](https://forum.supercharge.info/t/openstreetmap/3281)  
> 13. Need USA super chargers list to import into Teslamate \#3784 \- GitHub, [https://github.com/teslamate-org/teslamate/discussions/3784](https://github.com/teslamate-org/teslamate/discussions/3784)
