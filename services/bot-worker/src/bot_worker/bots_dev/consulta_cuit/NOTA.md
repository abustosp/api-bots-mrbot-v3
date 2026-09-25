# consulta_cuit (fixture dev)

Ejemplo de entrada para `ConsultaCuitPlugin.validate`. Es una carga de prueba,
no contiene credenciales y no ejecuta solicitudes de red. Para ejecución real,
la central entrega la configuración del endpoint dentro del sobre sellado.

La consulta usa la operación `consultar` y el campo `cuit`; no necesita una
sesión de navegador ni credenciales fiscales del usuario.
