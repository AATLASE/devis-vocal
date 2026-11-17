# Product

## Register

product

## Users

Artisans du bâtiment (plomberie, électricité, peinture, menuiserie, rénovation), le
plus souvent seuls ou à deux ou trois. Ils sortent d'une visite de chantier, en
voiture ou sur le trottoir, téléphone en main, parfois gantés, souvent en plein
soleil. Le devis est la corvée du soir : 20 à 45 minutes derrière un écran, repoussée,
envoyée tard, et l'affaire part chez le concurrent.

Le travail à faire : dicter le chantier en une ou deux minutes et repartir avec un
devis PDF qu'on peut envoyer au client sans y toucher.

Le site vitrine s'adresse aux mêmes personnes, mais avant le rendez-vous : un artisan
à qui on a parlé du produit, qui vient vérifier que c'est sérieux.

## Product Purpose

Devis Vocal transforme une note vocale brouillonne en devis chiffré, conforme aux
mentions obligatoires françaises. C'est aujourd'hui un démonstrateur : il sert à
montrer le concept en rendez-vous et à vérifier qu'un artisan paierait.

Réussite : un vocal réaliste en entrée, un PDF envoyable en sortie, et des totaux
justes à tous les coups (l'arithmétique est faite en Python, jamais par le modèle).

## Brand Personality

Atelier sérieux. Sobre, direct, chaleureux sans familiarité, vouvoiement. Le produit
doit ressembler à un bon devis imprimé : du papier, de l'encre, un seul accent brique,
une typographie qui porte. On parle métier (chantier, métré, acompte, décennale), pas
start-up.

Émotion visée : la confiance de quelqu'un qui connaît son travail. « Ces gens-là ont
compris comment je bosse. »

## Anti-references

- Les landing pages SaaS à dégradés violets, glassmorphism et métriques géantes.
- Le look « logiciel de gestion pour TPE » : bleu marine, icônes clipart, captures
  d'écran en perspective.
- Les faux témoignages, les compteurs inventés, les logos de clients qui n'existent pas.
- Le jargon IA (« propulsé par l'IA », « révolutionnez ») : l'artisan veut un devis,
  pas une technologie.

## Design Principles

1. **Montrer le devis, pas la machine.** Le PDF est la vitrine ; tout le reste le sert.
2. **Lisible dehors.** Contrastes AA mesurés, corps 16px minimum, cibles de 48px pour
   un pouce ganté.
3. **Honnêteté de démonstrateur.** Rien d'inventé : pas de chiffres, pas de clients,
   pas de témoignages qu'on n'a pas.
4. **Le calcul est sacré.** Un total faux tue la confiance ; l'interface ne calcule
   jamais à la place du serveur.
5. **Le compte ne conditionne jamais la dictée.**

## Accessibility & Inclusion

WCAG 2.1 AA. Contrastes vérifiés sur le fond le plus sombre de l'application (voir
`app/static/tokens.css`). Usage extérieur au soleil, sur téléphone : la discrétion se
fait par la taille et la graisse, jamais en délavant le texte. Respect de
`prefers-reduced-motion`. L'accent brique est toujours doublé d'un filet, d'une
graisse ou d'un symbole pour survivre au noir et blanc.
