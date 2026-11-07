"""Conservative clothing classification for built-in character feature tags.

The literal below was reviewed against all 3,455 distinct feature tags in the
local 7,999-character Anima official catalog, plus basic garment names. It is
independent of the mutable clothing pool and does not load external data.

Remove garments, footwear, gloves, explicit clothing construction and wearing
states. Retain hats, hair ornaments, eyewear, jewelry and standalone accessories;
retain body exposure, anatomy, tan lines and decorations without a clear garment
location. For example, a sailor collar is garment construction, while a detached
collar is an accessory. Unknown tags and unknown prompt syntax remain untouched.

Keep entries explicit and alphabetized. Do not replace this set with substring,
suffix, regular-expression or clothing-pool inference: "braid", "maid headdress",
"collarbone" and "mobile suit" are deliberately not clothing matches.
"""

from __future__ import annotations


_CLOTHING_TAGS: frozenset[str] = frozenset(
    """
a certain high school gym uniform
a certain high school uniform
achiga school uniform
advanced nurturing high school uniform
aether foundation uniform
aihara academy school uniform
ainu clothes
akatsuki uniform
american flag bikini
american flag dress
amestris military uniform
ancient greek clothes
angel's 24 uniform (blue archive)
animal costume
anzio school uniform
apron
aqua bikini
aqua dress
aqua gloves
aqua jacket
aqua shirt
aqua skirt
arabian clothes
aran sweater
argyle clothes
argyle dress
argyle sweater
aria company uniform
arm warmers
armor
armored boots
armored dress
armored leotard
armpit cutout
asahigaoka school uniform
ashford academy school uniform
asticassia school uniform
asymmetrical bodysuit
asymmetrical clothes
azumanga daioh's school uniform
b.a.b.e.l. uniform
babydoll
backless dress
backless outfit
backless shirt
baggy clothes
baggy pants
bandeau
barefoot
basketball jersey
basketball uniform
bath yukata
bc freedom military uniform
belt bra
bike jersey
bike shorts
bike shorts under skirt
bikini armor
bikini bottom only
bikini skirt
bikini top only
bikini under clothes
black apron
black armor
black bikini
black bodysuit
black bra
black camisole
black cape
black capelet
black cloak
black coat
black coat (kingdom hearts)
black corset
black dress
black footwear
black gloves
black hakama
black hoodie
black jacket
black kimono
black leotard
black male swimwear
black one-piece swimsuit
black order uniform
black panties
black pants
black pantyhose
black robe
black sailor collar
black serafuku
black shirt
black shorts
black skirt
black sleeves
black sports bra
black suit
black sweater
black sweater vest
black tank top
black thighhighs
black undershirt
black vest
blazer
blood on clothes
bloomers
blouse
blue apron
blue armor
blue bikini
blue bodysuit
blue buruma
blue cape
blue capelet
blue cloak
blue coat
blue dress
blue footwear
blue gloves
blue hakama
blue hoodie
blue jacket
blue jumpsuit
blue kimono
blue leotard
blue one-piece swimsuit
blue overalls
blue panties
blue pants
blue robe
blue sailor collar
blue sarong
blue serafuku
blue shirt
blue shorts
blue skirt
blue sleeves
blue sweater
blue tabard
blue tunic
blue vest
bobby socks
bodystocking
bodysuit
bodysuit under clothes
bomber jacket
bonyari high school uniform
boobplate
boots
bow bikini
bow skirt
boxing gloves
bra
bra peek
bra strap
bracer
breast curtain
breast curtains
breast cutout
breast pocket
breastless clothes
breastplate
bridal gauntlets
brown apron
brown bra
brown cape
brown capelet
brown cardigan
brown cloak
brown coat
brown corset
brown dress
brown footwear
brown gloves
brown hoodie
brown jacket
brown kimono
brown mittens
brown sailor collar
brown shirt
brown shorts
brown skirt
brown sweater
brown vest
bruce lee's jumpsuit
bubble skirt
budget sarashi
bulletproof vest
bunnysuit
buruma
business suit
buster shirt
bustier
button gap
buttons
c-string
camisole
camouflage jacket
camouflage shirt
camouflage skirt
cape
capelet
capri pants
cardigan
cardigan around waist
cassock
casual one-piece swimsuit
celestial being uniform
chainmail
chaldea uniform
champion uniform
champion's tunic (zelda)
chaps
checkered clothes
checkered kimono
checkered skirt
chest guard
chest sarashi
chi-hatan military uniform
china dress
chinese clothes
clawed gauntlets
cleavage cutout
cloak
clothes around waist
clothes writing
clothing cutout
coat
coat on shoulders
cocktail dress
collared cape
collared dress
collared jacket
collared shirt
competition school swimsuit
competition swimsuit
corset
costume
cow print bikini
criss-cross halter
crop top
crop top overhang
cropped hoodie
cropped jacket
cropped shirt
cropped vest
cross pasties
cross-laced clothes
cross-laced dress
cross-laced skirt
cross-laced sleeves
crotch plate
crotch seam
cutoffs
cutout above navel
cycling uniform
delmogeny uniform
demon slayer uniform
denim shorts
detached sleeves
deva battle suit
diamond clan outfit
diamond cutout
dirndl
dolphin shorts
double-breasted
dougi
dress
dress bow
dress flower
dress shirt
dress swimsuit
dudou
duel academy uniform (yu-gi-oh! gx)
eden academy school uniform
elbow gloves
epaulettes
evening gown
eyepatch bikini
fast food uniform
faulds
feather coat
feather dress
feather-trimmed sleeves
fingerless gloves
firefighter jacket
first high school uniform
flamenco dress
fleur de lapin uniform
floral print bikini
fortified suit
fourth east high school uniform
frilled apron
frilled bikini
frilled capelet
frilled dress
frilled gloves
frilled one-piece swimsuit
frilled panties
frilled sailor collar
frilled shirt
frilled shirt collar
frilled shorts
frilled skirt
frilled sleeves
front-tie bikini top
front-tie top
full armor
full-length zipper
fundoshi
funeral dress
fur cape
fur capelet
fur cloak
fur coat
fur jacket
fur-trimmed cape
fur-trimmed capelet
fur-trimmed cloak
fur-trimmed coat
fur-trimmed dress
fur-trimmed gloves
fur-trimmed jacket
fur-trimmed kimono
fur-trimmed shirt
fur-trimmed shorts
fur-trimmed sleeves
gakuran
galaxy expedition team survey corps uniform
gambeson
garreg mach monastery uniform
gauntlets
gekkoukan high school uniform
gem uniform (houseki no kuni)
german flag bikini
ginkgo guild uniform
glove
gloves
gold armor
gown
greco-roman clothes
green apron
green armor
green bikini
green bodysuit
green cape
green capelet
green cloak
green coat
green dress
green gloves
green hakama
green hoodie
green jacket
green jumpsuit
green kimono
green leotard
green one-piece swimsuit
green pajamas
green pants
green sailor collar
green shirt
green shorts
green skirt
green tank top
green tunic
green vest
grey bikini
grey capelet
grey cardigan
grey coat
grey dress
grey gloves
grey hoodie
grey jacket
grey kimono
grey overalls
grey pants
grey sailor collar
grey serafuku
grey shirt
grey shorts
grey skirt
grey sleeves
grey sports bra
grey vest
griffin & kryuger military uniform
gym shirt
gym shorts
gym uniform
habit
hakama pants
hakama short skirt
hakama skirt
hakuo school uniform
half gloves
halloween costume
halter dress
halterneck
hamster costume
hanasakigawa school uniform
haneoka school uniform
hanfu
haori
haramaki
harem outfit
harem pants
hasu no sora school uniform
hawaiian shirt
heart cutout
heattech leotard
herensuge girls academy school uniform
high collar
high heel boots
high heels
high-visibility vest
high-waist pants
high-waist shorts
high-waist skirt
highleg
highleg bikini
highleg leotard
highleg panties
highleg swimsuit
hikarizaka private high school uniform
himeya company uniform
hogwarts school uniform
hooded bodysuit
hooded cape
hooded capelet
hooded cloak
hooded coat
hooded jacket
hooded leotard
hooded robe
hoodie
hoshinoumi academy school uniform
hyakkaou academy school uniform
icho private high school uniform
idol clothes
impossible bodysuit
impossible leotard
indian clothes
itan private high school uniform
iwatobi high school uniform
jacket
jacket around waist
jacket on shoulders
jacket over swimsuit
japanese clothes
jeans
jester costume
jujutsu tech uniform
juliet sleeves
jumpsuit
kamiyama high school uniform (hyouka)
kanba girls high school uniform
karasuno volleyball uniform
kariginu
kariyushi shirt
kataginu
kazamatsuri institute high school uniform
kazekoshi school uniform
keizoku military uniform
kesa
kibina high school uniform
kibito high school uniform
kigurumi
kimono
kindergarten uniform
kirin (armor)
kissuisou uniform
kita high school uniform
kitauji high school uniform
kiyosumi school uniform
knee boots
kneehighs
knights of the round uniform
kousaka kirino's school uniform
kunugigaoka middle school uniform
kuromorimine military uniform
kusazuri
lab coat
lace gloves
lace sleeves
lace-up boots
lapels
latex bodysuit
latex leotard
layered bikini
layered dress
layered skirt
layered sleeves
leather armor
leather gloves
leather jacket
leather pants
leg armor
leg warmers
leggings
leotard
leotard under clothes
letterman jacket
lillian girls' academy school uniform
lingerie
little busters! school uniform
living doll uniform
loincloth
long coat
long dress
long skirt
long sleeves
lowleg pants
lowleg skirt
luna nova school uniform
lycoris uniform
mahora academy middle school uniform
maid apron
maid bikini
mandarin collar
megurigaoka high school uniform
meiji schoolgirl uniform
metamoran vest
micro shorts
microdress
microskirt
midriff sarashi
mihama junior high school uniform
military jacket
military uniform
millennium cheerleader outfit (blue archive)
miniskirt
mismatched bikini
mismatched gloves
mismatched sleeves
mittens
mobile trace suit
motosu school uniform
multi-strapped bikini
multi-strapped bikini bottom
multi-strapped bikini top
multicolored bikini
multicolored dress
multicolored jacket
multicolored kimono
multicolored leotard
multicolored shirt
multicolored skirt
multicolored sleeves
multicolored swimsuit
myoudou gakuen high school uniform
nami junior high school uniform
nanamori school uniform
naoetsu high school uniform
naranja academy school uniform
national shin ooshima school uniform
national verbena academy school uniform
naval uniform
navel cutout
negligee
new school swimsuit
night raven college uniform
nightgown
nightshirt
no bra
no panties
no pants
no shirt
no shoes
normal suit
o-ring bikini
o-ring top
odaiba girls high school uniform
off shoulder
off-shoulder bikini
off-shoulder dress
off-shoulder jacket
off-shoulder one-piece swimsuit
off-shoulder shirt
off-shoulder sweater
ofuda on clothes
old school swimsuit
one-piece swimsuit
ooarai naval school uniform
ooarai school uniform
oohashi high school uniform
open bodysuit
open cardigan
open clothes
open coat
open collar
open dress
open fly
open hoodie
open jacket
open kimono
open pants
open shirt
open shorts
open vest
orange bikini
orange bodysuit
orange dress
orange footwear
orange gloves
orange hoodie
orange jacket
orange jumpsuit
orange kimono
orange leotard
orange pants
orange planet uniform
orange sailor collar
orange shirt
orange shorts
orange skirt
orange sleeves
orange vest
ornate armor
ouran high school uniform
overall shorts
overalls
oversized breast cup
oversized clothes
oversized shirt
overskirt
padded cloak
pajamas
palette suit
panties
panties over pantyhose
pants
pants under skirt
panty peek
panty straps
pantyhose
pantyhose under shorts
paradis military uniform
partially fingerless gloves
partially unbuttoned
partially unzipped
patchwork clothes
pauldrons
paw gloves
paw shoes
pegasus knight uniform (fire emblem)
pelvic curtain
pencil dress
pencil skirt
peplos
petticoat
pilot suit
pinafore dress
pink apron
pink arm warmers
pink armor
pink babydoll
pink bikini
pink bodysuit
pink bra
pink camisole
pink capelet
pink cardigan
pink coat
pink dress
pink footwear
pink gloves
pink hakama
pink jacket
pink kimono
pink leotard
pink one-piece swimsuit
pink pajamas
pink pants
pink sailor collar
pink shirt
pink skirt
pink sleeves
pink sweater
pinstripe jacket
pinstripe shirt
pinstripe suit
plaid bikini
plaid dress
plaid jacket
plaid shirt
plaid skirt
plaid vest
pleated dress
pleated skirt
plugsuit
plunging neckline
police uniform
polka dot pajamas
polka dot panties
polo shirt
poncho
popped collar
power armor
power suit
praetor suit
pravda military uniform
pravda school uniform
print bikini
print bodysuit
print dress
print gloves
print jacket
print kimono
print leotard
print shirt
print skirt
print sweater
prison clothes
puffy detached sleeves
puffy long sleeves
puffy short sleeves
puffy sleeves
purple arm warmers
purple bikini
purple bodysuit
purple capelet
purple cardigan
purple coat
purple corset
purple dress
purple footwear
purple gloves
purple jacket
purple kimono
purple leotard
purple pants
purple sailor collar
purple shirt
purple shorts
purple skirt
purple sleeves
raglan sleeves
raimon soccer uniform
rainbow bikini
raincoat
raira academy school uniform
raizen high school uniform
rash guard
red apron
red armor
red bikini
red bodysuit
red bra
red buruma
red cape
red capelet
red cloak
red coat
red dress
red footwear
red gloves
red hakama
red jacket
red kimono
red leotard
red one-piece swimsuit
red panties
red pants
red robe
red sailor collar
red shirt
red shorts
red skirt
red sleeves
red suit
red sweater
red tank top
red track suit
red vest
reien girl's academy school uniform
revealing clothes
ribbed dress
ribbed leotard
ribbed shirt
ribbed sleeves
ribbed sweater
ribbon-trimmed sleeves
robe
roman clothes
romper
roswaal mansion maid uniform
roubai academy school uniform (old)
rouman academy school uniform
royal robe
rugby uniform
ryouou school uniform
saikai academy school uniform
sailor bikini
sailor collar
sailor dress
sailor senshi uniform
sailor shirt
sainan high school uniform
saiyan armor
sakugawa school uniform
sakuragaoka high school uniform
sandals
santa costume
sarashi
sarong
saunders military uniform
scale armor
school swimsuit
school uniform
see-through jacket
see-through leotard
see-through shirt
see-through skirt
see-through sleeves
selection university military uniform
selene girls school uniform
senriyama school uniform
serafuku
seven sisters high school uniform
shark costume
shell bikini
shimokitazawa high school uniform
shinda sekai sensen uniform
shiraitodai school uniform
shirt
shirt around waist
shirt tucked in
shoes
short dress
short kimono
short over long sleeves
short shorts
short sleeves
short-sleeved jacket
shorts
shorts under skirt
shoulder armor
shoulder boards
shoulder cutout
shoulder guard
shoulder pads
showgirl skirt
shrug (clothing)
shuka high school uniform
shuuchiin academy school uniform
shuujin academy school uniform
side slit
side slit shorts
side-tie bikini bottom
side-tie panties
side-tie shirt
side-tie skirt
sideless kimono
sideless outfit
sideless shirt
silver dress
single detached sleeve
single elbow glove
single gauntlet
single glove
single pantsleg
single pauldron
single sleeve
single-shoulder dress
skin tight
skirt
sleeve bow
sleeve cuffs
sleeveless
sleeveless bodysuit
sleeveless coat
sleeveless dress
sleeveless duster
sleeveless hoodie
sleeveless jacket
sleeveless kimono
sleeveless shirt
sleeveless turtleneck
sleeves past fingers
sleeves past wrists
sleeves rolled up
sneakers
sobu high school uniform
soccer uniform
socks
sophist's robe (ff14)
spacesuit
spaghetti strap
spiked gauntlets
spiked pauldrons
sports bra
sportswear
st. chronica academy school uniform
st. gloriana's military uniform
st. gloriana's school uniform
st. michael's school uniform
st. theresa's girls academy school uniform
star pasties
stile uniform
stocking
stockings
stomach cutout
straitjacket
strap slip
strapless
strapless bikini
strapless bra
strapless dress
strapless leotard
strapless one-piece swimsuit
strapless shirt
string bikini
striped bandeau
striped bikini
striped clothes
striped coat
striped dress
striped gloves
striped hoodie
striped jacket
striped one-piece swimsuit
striped panties
striped pants
striped shirt
striped sleeves
striped sweater
striped thighhighs
suit
suit jacket
sukajan
summer uniform
sundress
superhero costume
surcoat
suspender shorts
suspender skirt
sweater
sweater around waist
sweater dress
sweater under shirt
sweater vest
swimsuit
swimsuit cover-up
swimsuit under clothes
swimwear
t-shirt
tabard
tactical clothes
taichou haori
taimanin suit
tangzhuang
tank top
taut clothes
team magma uniform
team rocket uniform
team skull uniform
teitan high school uniform
tennis uniform
thigh boots
thigh cutout
thighband pantyhose
thighhighs
thong
thong leotard
tied jacket
tied shirt
tied sleeves
tight clothes
tight pants
tight shirt
tights
toga
tohmi higashi high school uniform
tokisadame school uniform
tokiwadai school uniform
tomoeda elementary school uniform
tootsuki saryou ryouri gakuen uniform
torn cape
torn clothes
torn coat
torn dress
torn sweater
tracen school uniform
track jacket
track pants
track suit
translucent bunnysuit
trench coat
trousers
tsukinomori school uniform
tsuruga school uniform
tube top
tunic
turtleneck
turtleneck jacket
turtleneck sweater
tutu
tuxedo
two-sided cape
two-sided coat
two-sided dress
two-tone bikini
two-tone bodysuit
two-tone dress
two-tone gloves
two-tone hoodie
two-tone leotard
two-tone shirt
two-tone skirt
two-tone sports bra
two-tone sweater
two-tone swimsuit
uchikake
underboob cutout
underbust
undershirt
underwear
uneven sleeves
uniform
unzipped
uranohoshi school uniform
uva academy school uniform
v-neck
vambraces
vermilion city school uniform
vertical-striped bikini
vertical-striped bodysuit
vertical-striped clothes
vertical-striped dress
vertical-striped jacket
vertical-striped pajamas
vertical-striped shirt
vertical-striped skirt
very long sleeves
vest
vietnamese dress
virgin killer outfit
volleyball uniform
waist apron
waist cape
waistcoat
wandenreich uniform
wedding dress
wet shirt
wetsuit
white apron
white armor
white bandeau
white bikini
white bodysuit
white camisole
white cape
white capelet
white cardigan
white cloak
white coat
white dress
white footwear
white gloves
white hoodie
white jacket
white kimono
white leotard
white mittens
white one-piece swimsuit
white panties
white pants
white robe
white romper
white sailor collar
white shirt
white shorts
white shrug
white skirt
white sleeves
white socks
white suit
white sweater
white tank top
white thighhighs
white tube top
white tunic
white uniform
white vest
wide sleeves
wing collar
winter coat
winter uniform
wrestling outfit
yamabuki high school uniform
yamaku high school uniform
yasogami school uniform
yellow bikini
yellow bodysuit
yellow camisole
yellow cardigan
yellow dress
yellow footwear
yellow gloves
yellow jacket
yellow kimono
yellow leotard
yellow raincoat
yellow shirt
yellow shorts
yellow skirt
yellow sleeves
yellow sweater
yellow sweater vest
yellow vest
yokosuka girls marine high school uniform
yomiyama north junior high school uniform
youtou high school uniform
yuigaoka school uniform
yukata
yumenosaki school uniform
yurigaoka girls academy school uniform
yuyushiki's school uniform
zero suit
zipper
zipper dress
zipper pull tab
zipper skirt
""".strip().splitlines()
)


def is_character_clothing_tag(value: str) -> bool:
    """Match one complete tag without rewriting it or interpreting prompt syntax."""
    normalized = " ".join(value.casefold().replace("_", " ").split())
    return normalized in _CLOTHING_TAGS
