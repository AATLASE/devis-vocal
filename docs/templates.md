# Écrire son template de devis

> **Hors du périmètre d'origine.** `CLAUDE.md` classe « comptes / auth / multi-tenant »
> et « base de données » dans ce qu'il ne faut pas construire. Le téléversement d'un
> template a été ajouté sur décision explicite, en connaissance du coût décrit plus bas.

Par défaut, tous les devis sortent sur `templates/devis.html` — la mise en page livrée,
celle qui porte les mentions obligatoires et dont la pagination est calée au pixel. Un
artisan connecté peut la remplacer par la sienne depuis **Mon template**.

Sans Firebase configuré sur le serveur, la fonctionnalité n'existe pas : les routes
`/api/template*` et `/api/profil` répondent 404, et tous les devis sortent sur le
template livré. Sans comptes, tout le monde serait le même utilisateur, et le template
posé par l'un s'imprimerait sur les devis de tous les autres.

## Ce que vous perdez en faisant ça

Une phrase, et elle est importante : **le template livré garantit la conformité du devis,
le vôtre ne garantit rien.** C'est un document HTML libre, et rien n'oblige un document
libre à imprimer un SIRET.

Le produit ne peut pas empêcher ça, alors il le mesure. À l'enregistrement, votre
template est rendu sur un devis d'exemple et le résultat est passé au crible de quatorze
contrôles. Ce qui manque est affiché sur l'écran du template, et y reste. Un template non
conforme est accepté — c'est votre document — mais vous savez ce qu'il lui manque.

Les quatorze mentions cherchées, dans le document **rendu** :

| Mention | Où la prendre |
|---|---|
| Le numéro du devis | `devis.numero` |
| La date d'émission | `devis.date_emission` |
| La date de fin de validité | `devis.date_validite` |
| Le nom de l'entreprise | `e.nom` |
| L'adresse de l'entreprise | `e.adresse`, `e.code_postal_ville` |
| Le numéro SIRET | `e.siret` |
| Le numéro de TVA intracommunautaire | `e.tva_intracom` |
| L'assurance professionnelle | `e.assurance` **et** `e.assurance_police` |
| Le nom du client | `devis.client.nom` |
| Le détail des prestations | `devis.lignes` |
| Le total HT | `devis.total_ht` |
| Le montant de la TVA | `devis.montant_tva` |
| Le total TTC | `devis.total_ttc` |
| La mention « bon pour accord » | à écrire en toutes lettres |

Le contrôle cherche les **valeurs**, pas les libellés : écrire `SIRET :` sans imprimer
`{{ e.siret }}` ne compte pas. C'est délibéré — un intitulé sans valeur ne rend pas un
devis conforme. Pour que ces valeurs ne se confondent pas entre elles, le devis
d'exemple est établi au nom d'une entreprise fictive, l'« Atelier Exemple ».

Seul compte le texte que le PDF imprime : pas celui de `<title>`, de `<style>`, de
`<script>`, de `<template>`, ni d'un commentaire. Le template livré met le numéro du
devis dans son `<title>` ; si vous le retirez du corps, le contrôle le signale. Une
limite, en revanche : le contrôle lit le HTML, pas le CSS. Un texte masqué par
`display: none` compte encore comme imprimé.

## Par où commencer

Pas par une page blanche. Le bouton **Télécharger le template livré comme point de
départ** vous donne `templates/devis.html`, qui passe déjà les quatorze contrôles.
Modifiez-le plutôt que de repartir de zéro.

Puis, toujours dans cet ordre :

1. **Voir l'aperçu** — le template est rendu en PDF sur un devis d'exemple complet
   (cinq lignes, deux observations, des prix estimés), au nom de l'entreprise fictive.
   Rien n'est enregistré.
2. **Enregistrer ce template** — une fois que l'A4 vous convient.

## Le langage

C'est du [Jinja2](https://jinja.palletsprojects.com/). Trois formes suffisent :
`{{ valeur }}` pour insérer, `{% for %}` pour boucler, `{% if %}` pour conditionner.

### Les variables disponibles

| Nom | Contenu |
|---|---|
| `devis` | le devis chiffré — voir `app/models.py`, classe `Devis` |
| `e` | l'entreprise, raccourci de `devis.entreprise` |
| `pages` | les lignes découpées page par page, `[(numéro, ligne), …]` |
| `cloture_sur_derniere` | la clôture tient sous la dernière tranche du tableau |
| `total_pages` | le nombre de pages du document |
| `css` | la feuille du template livré, polices comprises, prête à inliner |
| `taux_reduit` | `0.10`, pour distinguer le taux réduit du normal |

Les trois du milieu ne servent qu'à reproduire la pagination du template livré. Une mise
en page simple les ignore et laisse Chromium couper les pages.

Sur le PDF, `css` porte les polices en base64 : le document doit rester juste sans
réseau. Dans l'aperçu affiché par l'application, ces 518 Ko sont remplacés par un
`@import` vers `/static/fonts.css` — la page les a déjà chargées, et les renvoyer à
chaque devis se paierait sur la 4G d'une camionnette. Un template qui se contente
d'inliner `{{ css }}` ne voit pas la différence ; un template qui découperait cette
chaîne, si.

Toutes les variables `--dv-*` de `app/static/tokens.css` y sont, et un template peut
s'en servir. Celles du second bloc — ombres, durées, courbes de mouvement — n'ont de
sens qu'à l'écran : elles ne s'impriment pas.

### Les champs du devis

`devis.numero` · `devis.date_emission` · `devis.date_validite` · `devis.validite_jours` ·
`devis.client.nom` `.adresse` `.telephone` · `devis.type_travaux` · `devis.duree_estimee` ·
`devis.lignes` · `devis.notes` · `devis.taux_tva` · `devis.taux_tva_libelle` ·
`devis.total_ht` · `devis.montant_tva` · `devis.total_ttc` · `devis.acompte_pct` ·
`devis.montant_acompte` · `devis.total_ht_estime` · `devis.a_des_prix_estimes`

Chaque ligne porte : `designation` · `detail` · `quantite` · `unite.value` ·
`prix_unitaire_ht` · `total_ht` · `a_valider`.

`a_valider` vaut `true` quand le prix a été estimé et non dicté. **Marquez-le.** C'est
une règle du produit, pas une décoration : un prix estimé présenté comme ferme est un
prix qu'on devra renier devant le client.

### Les filtres de formatage

Les mêmes que le template livré, et il faut les utiliser : ils produisent la typographie
française — espace fine insécable entre les milliers, virgule décimale.

| Filtre | `4546.45` donne |
|---|---|
| `montant` | `4 546,45` |
| `euro` | `4 546,45 €` |
| `nombre` | `18,5` — sans décimale inutile |
| `pourcent` | `0.30` → `30` |
| `date_fr` | `12/03/2026` |
| `date_longue` | `12 mars 2026` |

```jinja
<p>Total TTC {{ devis.total_ttc | euro }}</p>
<p>Acompte {{ devis.acompte_pct | pourcent }} % : {{ devis.montant_acompte | euro }}</p>
```

**N'écrivez jamais un calcul dans le template.** Tous les totaux arrivent déjà calculés
par `to_devis()`, en Python, avec l'arrondi commercial français. C'est la règle
d'architecture centrale du projet : un total faux devant un artisan tue la démonstration.

## Ce qui est refusé, et pourquoi

Le template est refusé à l'enregistrement — jamais au moment où vous voulez votre PDF
devant un client — dans cinq cas :

- **fichier vide** ;
- **plus de 512 Ko** — les images doivent être des URI `data:` compactes ;
- **syntaxe Jinja fautive** — une balise `{% for %}` non refermée, par exemple ;
- **erreur au rendu** sur le devis d'exemple ;
- **rendu démesuré** — plus de 5 secondes de calcul, plus de 10 millions de caractères
  produits, ou une multiplication ou une puissance géante. Un template de devis se rend
  en quelques millisecondes : ces bornes n'arrêtent que ce qui ferait tomber le serveur.

Deux choses sont retirées sans que le template soit refusé :

- **le JavaScript** — balises `<script>` et attributs `onclick`, `onload`… Un devis est
  un document, pas une application ;
- **l'accès aux internes de Python** — le template est rendu dans un bac à sable Jinja.
  `{% include %}` et `{% extends %}` échouent : aucun template ne lira un fichier du
  serveur.

Et quoi que contienne le template, le PDF est imprimé par un Chromium **sans JavaScript
et sans réseau**. Une image, une police, une feuille de style ou un cadre désignés par
une URL ne sont pas chargés : tout ce que le document affiche doit voyager avec lui, en
URI `data:`. C'est déjà le cas des polices de `{{ css }}`.

## Le filet, en production

Votre template a été validé sur le devis d'exemple : cinq lignes, un client renseigné.
Un vrai devis peut le mettre en défaut — vingt lignes, ou un champ nul là où vous
attendiez du texte.

Dans ce cas, le serveur **rejoue le devis sur le template livré** plutôt que de vous
laisser sans document. Le PDF sort, complet et conforme, mais sur l'autre mise en page —
et un bandeau vous le dit. Il ne s'affiche pas pour rien : allez voir votre template.

Pour vous en prémunir, protégez les champs qui peuvent être absents :

```jinja
{% if devis.client.nom %}<p>{{ devis.client.nom }}</p>{% endif %}
{{ devis.duree_estimee or "à convenir" }}
```

## Revenir en arrière

**Revenir au template livré**, en bas de l'écran. Vos devis repartent immédiatement sur
la mise en page d'origine, celle dont la conformité est garantie.
