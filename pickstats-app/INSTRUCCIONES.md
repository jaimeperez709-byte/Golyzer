# Golyzer — de prototipo web a app en tu celular

## Qué es esto

Esta carpeta convierte la app que ya viste (`www/index.html`) en un proyecto de **Capacitor**, una herramienta que toma una app web y la empaqueta como app nativa de iOS y Android — reutilizando el 100% del código que ya construimos (ligas, modelo de Poisson, combinadas, filtro por país), en vez de reescribir todo desde cero.

## Una aclaración importante sobre "quién la crea"

Yo ya escribí todo el código: la lógica, el diseño, el modelo estadístico y ahora el empaquetado como app. Lo que **no puedo hacer por ti** son los pasos finales que requieren tu identidad y tu dinero: crear cuenta de desarrollador de Apple/Google, firmar la app con tu certificado, y darle "publicar" — eso solo lo puede hacer el dueño de las cuentas (tú), igual que nadie más puede firmar un contrato en tu nombre. Yo te voy guiando comando por comando; tú los pegas en tu Terminal (tienes Mac, así que tienes todo lo necesario).

## Lo que necesitas instalar (una sola vez, gratis)

1. **Xcode** — para compilar la versión de iOS. Se instala gratis desde la Mac App Store (pesa varios GB, déjalo descargando de fondo).
2. **Android Studio** — para compilar la versión de Android. Gratis en [developer.android.com/studio](https://developer.android.com/studio).
3. **Node.js** — probablemente ya lo tienes si corriste los comandos de `curl` anteriores; si no, instálalo desde [nodejs.org](https://nodejs.org).

## Pasos para probar la app en tu propio celular (gratis, sin publicar aún)

Abre Terminal, entra a esta carpeta y corre:

```bash
cd ruta/a/pickstats-app
npm install
npx cap add ios
npx cap add android
npx @capacitor/assets generate
npx cap sync
```

Ese `npx @capacitor/assets generate` toma el ícono que ya dejamos en `resources/icon.png` y genera automáticamente todos los tamaños que piden Apple y Google (desde el ícono grande de la tienda hasta el chiquito de la pantalla de inicio).

Esto crea dos carpetas nuevas: `ios/` y `android/` — son proyectos nativos completos, ya generados automáticamente, listos para abrir.

**Para probarla en un iPhone:**
```bash
npx cap open ios
```
Se abre Xcode. Conecta tu iPhone por cable, selecciónalo como destino arriba, y dale ▶️ (Run). La primera vez Xcode te pide "confiar" en tu cuenta de Apple gratuita — con eso ya la app corre en tu teléfono por 7 días (se renueva reabriendo el proyecto).

**Para probarla en un Android:**
```bash
npx cap open android
```
Se abre Android Studio. Conecta tu celular Android (con "depuración USB" activada en Ajustes) y dale ▶️ (Run).

## Pasos para publicarla de verdad en las tiendas

1. **Cuenta de Apple Developer** ($99/año): [developer.apple.com/programs](https://developer.apple.com/programs). Verificación de identidad, puede tardar 1-2 días.
2. **Cuenta de Google Play Console** ($25 pago único): [play.google.com/console](https://play.google.com/console/signup).
3. En Xcode: Product → Archive → subir a App Store Connect, llenar descripción/capturas/precio (gratis) → enviar a revisión.
4. En Android Studio: Build → Generate Signed Bundle → subir a Play Console → llenar ficha → enviar a revisión.
5. Apple revisa en 24-48h normalmente (primera app puede tardar más). Google revisa en 1-14 días (primera vez tarda más que apps posteriores).

Cuando llegues a cualquiera de estos pasos y te trabes con un error o una pantalla que no entiendes, pégame exactamente lo que ves (captura o texto) y seguimos desde ahí — es un proceso con muchos pasos pequeños, pero ninguno es complicado por sí solo.

## Cuando conectemos datos reales (API-Football / football-data.org)

Solo hay que reemplazar el array `matches` dentro de `www/index.html` por datos que vengan de la API (en vez de escribirlos a mano como ahora). Podemos automatizar eso con un pequeño script que tú corres localmente (porque, como ya vimos, mi entorno no tiene salida a esas APIs) y que regenera ese archivo cada vez que quieras actualizar los partidos.
