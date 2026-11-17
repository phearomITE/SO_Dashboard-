`Map_KML.kml` is the supplied commune boundary dataset. Run CHECK_SO.bat after replacing or adding boundary files.
Use WGS84 longitude/latitude. Supported properties (case insensitive):
- province / ADM1_EN / NAME_1 / PROV_NAME / province_name
- district / ADM2_EN / NAME_2 / DIST_NAME / district_name
- commune / ADM3_EN / NAME_3 / COM_NAME / commune_name
- commune_code / COM_CODE / Com.Code / ADM3_PCODE
- supplied schema: HRName, PRO_CODE, DIS_CODE, COM_CODE, with Khmer district in `0001- DEAL`
KML uses ExtendedData Data/value or SchemaData SimpleData. Placemark name is the fallback commune name.
Invalid self-intersecting polygons are repaired in memory with Shapely; the original KML is never rewritten.
Prefer the official commune code used in Plan_SO.xlsx. Without a common code, both district and commune names must match exactly (case insensitive).
Overlapping boundary matches, missing polygons, missing employee schedules, or unmapped names return Not checked with an explanation.
A GPS pin is evidence of the submitted location; it does not independently prove physical attendance.
