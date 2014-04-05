# -*- coding: utf-8 -*-
"""
/***************************************************************************
 Qgis2threejsDialog
                                 A QGIS plugin
 export terrain and map image into web browser
                             -------------------
        begin                : 2013-12-21
        copyright            : (C) 2013 Minoru Akagi
        email                : akaginch@gmail.com

 RectangleMapTool class is from extentSelector.py of GdalTools plugin
        copyright            : (C) 2010 by Giuseppe Sucameli
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

from PyQt4.QtCore import *
from PyQt4.QtGui import *
from qgis.core import *
from qgis.gui import QgsMessageBar, QgsMapToolEmitPoint, QgsRubberBand
from ui.ui_qgis2threejsdialog import Ui_Qgis2threejsDialog

from qgis2threejsmain import ObjectTreeItem, MapTo3D, OutputContext, exportToThreeJS
import qgis2threejstools as tools
from quadtree import *
from vectorobject import *
import propertypages as ppages


debug_mode = 1

class Qgis2threejsDialog(QDialog):
  STYLE_MAX_COUNT = 3

  def __init__(self, iface, properties=None):
    QDialog.__init__(self, iface.mainWindow())
    self.iface = iface
    self.apiChanged22 = False   # not QgsApplication.prefixPath().startswith("C:/OSGeo4W")  # QGis.QGIS_VERSION_INT >= 20200

    self.currentItem = None
    self.currentPage = None
    topItemCount = len(ObjectTreeItem.topItemNames)
    if properties is None:
      self.properties = [None] * topItemCount
      for i in range(ObjectTreeItem.ITEM_OPTDEM, topItemCount):
        self.properties[i] = {}
    else:
      self.properties = properties

    # Set up the user interface from Designer.
    self.ui = ui = Ui_Qgis2threejsDialog()
    ui.setupUi(self)

    self.setWindowFlags(self.windowFlags() | Qt.WindowMinimizeButtonHint)
    ui.lineEdit_OutputFilename.setPlaceholderText("[Temporary file]")

    ui.pushButton_Run.clicked.connect(self.run)
    ui.pushButton_Close.clicked.connect(self.reject)

    # set up map tool
    self.previousMapTool = None
    self.mapTool = RectangleMapTool(iface.mapCanvas())
    #self.mapTool = PointMapTool(iface.mapCanvas())

    # set up the properties pages
    self.pages = {}
    self.pages[ppages.PAGE_WORLD] = ppages.WorldPropertyPage(self)
    #self.pages[ppages.PAGE_CONTROLS] = ppages.ControlsPropertyPage(self)
    #self.pages[ppages.PAGE_PLANE] = ppages.PlanePropertyPage(self)
    self.pages[ppages.PAGE_DEM] = ppages.DEMPropertyPage(self)
    self.pages[ppages.PAGE_VECTOR] = ppages.VectorPropertyPage(self)
    container = ui.propertyPagesContainer
    for page in self.pages.itervalues():
      page.hide()
      container.addWidget(page)

    # build object tree
    self.topItemPages = {ObjectTreeItem.ITEM_WORLD: ppages.PAGE_WORLD, ObjectTreeItem.ITEM_CONTROLS: ppages.PAGE_CONTROLS, ObjectTreeItem.ITEM_PLANE: ppages.PAGE_PLANE, ObjectTreeItem.ITEM_DEM: ppages.PAGE_DEM}
    self.initObjectTree()
    self.ui.treeWidget.currentItemChanged.connect(self.currentObjectChanged)
    self.ui.treeWidget.itemChanged.connect(self.objectItemChanged)

    ui.progressBar.setVisible(False)
    ui.toolButton_Browse.clicked.connect(self.browseClicked)

    #iface.mapCanvas().mapToolSet.connect(self.mapToolSet)    # to show button to enable own map tool

    self.bar = None   # QgsMessageBar
    self.localBrowsingMode = True
    self.rb_quads = self.rb_point = None
    self.objectTypeManager = ObjectTypeManager()

  def exec_(self):
    ui = self.ui
    messages = []
    # show message if crs unit is degrees
    mapSettings = self.iface.mapCanvas().mapSettings() if self.apiChanged22 else self.iface.mapCanvas().mapRenderer()
    if mapSettings.destinationCrs().mapUnits() in [QGis.Degrees]:
      self.showMessageBar("The unit of current CRS is degrees", "Terrain may not appear well.")

    self.ui.treeWidget.setCurrentItem(self.ui.treeWidget.topLevelItem(ObjectTreeItem.ITEM_DEM))

    return QDialog.exec_(self)

  def showMessageBar(self, title, text, level=QgsMessageBar.INFO):
    if self.bar is None:
      self.bar = QgsMessageBar()
      self.bar.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

      ui = self.ui
      margins = ui.gridLayout.getContentsMargins()
      vl = ui.gridLayout.takeAt(0)
      ui.gridLayout.setContentsMargins(0,0,0,0)
      ui.gridLayout.addWidget(self.bar, 0, 0)
      ui.gridLayout.addItem(vl, 1, 0)
      ui.verticalLayout.setContentsMargins(margins[0], margins[1] / 2, margins[2], margins[3])
    self.bar.pushMessage(title, text, level=level)

  def initTemplateList(self, templateName=""):
    self.ui.comboBox_Template.clear()
    templateDir = QDir(tools.templateDir())
    for entry in templateDir.entryList(["*.html", "*.htm"]):
      self.ui.comboBox_Template.addItem(entry)

    if templateName:
      index = self.ui.comboBox_Template.findText(templateName)
      if index != -1:
        self.ui.comboBox_Template.setCurrentIndex(index)
      return index
    return -1

  def initObjectTree(self):
    tree = self.ui.treeWidget
    tree.clear()

    # add vector and raster layers into tree widget
    topItems = []
    for index, itemName in enumerate(ObjectTreeItem.topItemNames):
      item = QTreeWidgetItem(tree, [itemName])
      item.setData(0, Qt.UserRole, index)
      topItems.append(item)

    for layer in self.iface.legendInterface().layers():
      layerType = layer.type()
      if layerType not in (QgsMapLayer.VectorLayer, QgsMapLayer.RasterLayer):
        continue

      parentId = None
      if layerType == QgsMapLayer.VectorLayer:
        geometry_type = layer.geometryType()
        if geometry_type in [QGis.Point, QGis.Line, QGis.Polygon]:
          parentId = ObjectTreeItem.ITEM_POINT + geometry_type    # - QGis.Point
      elif layerType == QgsMapLayer.RasterLayer and layer.providerType() == "gdal" and layer.bandCount() == 1:
        parentId = ObjectTreeItem.ITEM_OPTDEM
      if parentId is None:
        continue

      item = QTreeWidgetItem(topItems[parentId], [layer.name()])
      isVisible = self.properties[parentId].get(layer.id(), {}).get("visible", False)   #self.iface.legendInterface().isLayerVisible(layer)
      check_state = Qt.Checked if isVisible else Qt.Unchecked
      item.setData(0, Qt.CheckStateRole, check_state)
      item.setData(0, Qt.UserRole, layer.id())

    for item in topItems:
      tree.expandItem(item)

  def saveProperties(self, item, page):
    properties = page.properties()
    parent = item.parent()
    if parent is None:
      # top item: properties[topItemIndex]
      self.properties[item.data(0, Qt.UserRole)] = properties
    else:
      # layer item: properties[topItemIndex][layerId]
      topItemIndex = parent.data(0, Qt.UserRole)
      self.properties[topItemIndex][item.data(0, Qt.UserRole)] = properties

    if debug_mode:
      qDebug(str(self.properties))

  def currentObjectChanged(self, currentItem, previousItem):
    # save properties of previous item
    if previousItem and self.currentPage:
      self.saveProperties(previousItem, self.currentPage)

    self.currentItem = currentItem
    self.currentPage = None
    # hide all pages
    for page in self.pages.itervalues():
      page.hide()

    parent = currentItem.parent()
    if parent is None:
      topItemIndex = currentItem.data(0, Qt.UserRole)
      pageType = self.topItemPages.get(topItemIndex, ppages.PAGE_NONE)
      page = self.pages.get(pageType, None)
      if page is None:
        return

      page.setup(self.properties[topItemIndex])
      page.show()

    else:
      parentId = parent.data(0, Qt.UserRole)
      layerId = currentItem.data(0, Qt.UserRole)
      if layerId is None:
        return

      layer = QgsMapLayerRegistry().instance().mapLayer(layerId)
      if layer is None:
        return

      layerType = layer.type()
      if layerType == QgsMapLayer.RasterLayer:
        page = self.pages[ppages.PAGE_DEM]
        page.setup(self.properties[parentId].get(layerId, None), layer, False)
      elif layerType == QgsMapLayer.VectorLayer:
        page = self.pages[ppages.PAGE_VECTOR]
        page.setup(self.properties[parentId].get(layerId, None), layer)
      else:
        return

      page.show()

    self.currentPage = page

  def objectItemChanged(self, item, column):
    parent = item.parent()
    if parent is None:
      return

    if item == self.currentItem:
      if self.currentPage:
        # update enablement of property widgets
        self.currentPage.itemChanged(item)
    else:
      # select changed item
      self.ui.treeWidget.setCurrentItem(item)

      # set visible property
      #visible = item.data(0, Qt.CheckStateRole) == Qt.Checked
      #parentId = parent.data(0, Qt.UserRole)
      #layerId = item.data(0, Qt.UserRole)
      #self.properties[parentId].get(layerId, {})["visible"] = visible

  def primaryDEMChanged(self, layerId):
    tree = self.ui.treeWidget
    parent = tree.topLevelItem(ObjectTreeItem.ITEM_OPTDEM)
    tree.blockSignals(True)
    for i in range(parent.childCount()):
      item = parent.child(i)
      isPrimary = item.data(0, Qt.UserRole) == layerId
      item.setDisabled(isPrimary)
    tree.blockSignals(False)

  def numericFields(self, layer):
    # get attributes of a sample feature and create numeric field name list
    numeric_fields = []
    f = QgsFeature()
    layer.getFeatures().nextFeature(f)
    for field in f.fields():
      isNumeric = False
      try:
        float(f.attribute(field.name()))
        isNumeric = True
      except:
        pass
      if isNumeric:
        numeric_fields.append(field.name())
    return numeric_fields

  def progress(self, percentage):
    self.ui.progressBar.setValue(percentage)
    self.ui.progressBar.setVisible(percentage != 100)

  def run(self):
    ui = self.ui
    filename = ui.lineEdit_OutputFilename.text()   # ""=Temporary file
    if filename != "" and QFileInfo(filename).exists() and QMessageBox.question(None, "Qgis2threejs", "Output file already exists. Overwrite it?", QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
      return
    self.endPointSelection()

    # save properties of current object
    item = self.ui.treeWidget.currentItem()
    if item and self.currentPage:
      self.saveProperties(item, self.currentPage)

    ui.pushButton_Run.setEnabled(False)
    self.progress(0)

    canvas = self.iface.mapCanvas()
    templateName = ui.comboBox_Template.currentText()
    controls = "TrackballControls.js"
    htmlfilename = ui.lineEdit_OutputFilename.text()

    # world properties
    world = self.properties[ObjectTreeItem.ITEM_WORLD] or {}
    verticalExaggeration = world.get("lineEdit_zFactor", 1.5)
    verticalShift = world.get("lineEdit_zShift", 0)

    # export to javascript (three.js)
    mapTo3d = MapTo3D(canvas, verticalExaggeration=float(verticalExaggeration), verticalShift=float(verticalShift))
    context = OutputContext(templateName, controls, mapTo3d, canvas, self.properties, self, self.objectTypeManager, self.localBrowsingMode)
    htmlfilename = exportToThreeJS(htmlfilename, context, self.progress)

    self.progress(100)
    ui.pushButton_Run.setEnabled(True)
    if htmlfilename is None:
      return
    self.clearRubberBands()

    if not tools.openHTMLFile(htmlfilename):
      return
    QDialog.accept(self)

  def reject(self):
    # save properties of current object
    item = self.ui.treeWidget.currentItem()
    if item and self.currentPage:
      self.saveProperties(item, self.currentPage)

    self.endPointSelection()
    self.clearRubberBands()
    QDialog.reject(self)

  def startPointSelection(self):
    canvas = self.iface.mapCanvas()
    if self.previousMapTool != self.mapTool:
      self.previousMapTool = canvas.mapTool()
    canvas.setMapTool(self.mapTool)
    self.pages[ppages.PAGE_DEM].toolButton_PointTool.setVisible(False)

  def endPointSelection(self):
    self.mapTool.reset()
    if self.previousMapTool is not None:
      self.iface.mapCanvas().setMapTool(self.previousMapTool)

  def mapToolSet(self, mapTool):
    return
    #TODO: unstable
    if mapTool != self.mapTool and self.currentPage is not None:
      if self.currentPage.pageType == ppages.PAGE_DEM and self.currentPage.isPrimary:
        self.currentPage.toolButton_PointTool.setVisible(True)

  def createRubberBands(self, quads, point=None):
    self.clearRubberBands()
    # create quads with rubber band
    self.rb_quads = QgsRubberBand(self.iface.mapCanvas(), QGis.Line)
    self.rb_quads.setColor(Qt.blue)
    self.rb_quads.setWidth(1)

    for quad in quads:
      points = []
      extent = quad.extent
      points.append(QgsPoint(extent.xMinimum(), extent.yMinimum()))
      points.append(QgsPoint(extent.xMinimum(), extent.yMaximum()))
      points.append(QgsPoint(extent.xMaximum(), extent.yMaximum()))
      points.append(QgsPoint(extent.xMaximum(), extent.yMinimum()))
      self.rb_quads.addGeometry(QgsGeometry.fromPolygon([points]), None)
      self.log(extent.toString())
    self.log("Quad count: %d" % len(quads))

    # create a point with rubber band
    if point:
      self.rb_point = QgsRubberBand(self.iface.mapCanvas(), QGis.Point)
      self.rb_point.setColor(Qt.red)
      self.rb_point.addPoint(point)

  def clearRubberBands(self):
    # clear quads and point
    if self.rb_quads:
      self.iface.mapCanvas().scene().removeItem(self.rb_quads)
      self.rb_quads = None
    if self.rb_point:
      self.iface.mapCanvas().scene().removeItem(self.rb_point)
      self.rb_point = None

  def browseClicked(self):
    directory = self.ui.lineEdit_OutputFilename.text()
    if directory == "":
      directory = QDir.homePath()
    filename = QFileDialog.getSaveFileName(self, self.tr("Output filename"), directory, "HTML file (*.html *.htm)", options=QFileDialog.DontConfirmOverwrite)
    if filename != "":
      self.ui.lineEdit_OutputFilename.setText(filename)

  def log(self, msg):
    if debug_mode:
      qDebug(msg)

class PointMapTool(QgsMapToolEmitPoint):
  def __init__(self, canvas):
    self.canvas = canvas
    QgsMapToolEmitPoint.__init__(self, self.canvas)
    self.point = None

  def canvasPressEvent(self, e):
    self.point = self.toMapCoordinates(e.pos())
    self.emit(SIGNAL("pointSelected()"))

class RectangleMapTool(QgsMapToolEmitPoint):
  def __init__(self, canvas):
    self.canvas = canvas
    QgsMapToolEmitPoint.__init__(self, self.canvas)

    self.rubberBand = QgsRubberBand(self.canvas, QGis.Polygon)
    self.rubberBand.setColor(QColor(255, 0, 0, 180))
    self.rubberBand.setWidth(1)
    self.reset()

  def reset(self):
    self.startPoint = self.endPoint = None
    self.isEmittingPoint = False
    self.rubberBand.reset(QGis.Polygon)

  def canvasPressEvent(self, e):
    self.startPoint = self.toMapCoordinates(e.pos())
    self.endPoint = self.startPoint
    self.isEmittingPoint = True
    self.showRect(self.startPoint, self.endPoint)

  def canvasReleaseEvent(self, e):
    self.isEmittingPoint = False
    self.emit(SIGNAL("rectangleCreated()"))

  def canvasMoveEvent(self, e):
    if not self.isEmittingPoint:
      return
    self.endPoint = self.toMapCoordinates(e.pos())
    self.showRect(self.startPoint, self.endPoint)

  def showRect(self, startPoint, endPoint):
    self.rubberBand.reset(QGis.Polygon)
    if startPoint.x() == endPoint.x() or startPoint.y() == endPoint.y():
      return

    point1 = QgsPoint(startPoint.x(), startPoint.y())
    point2 = QgsPoint(startPoint.x(), endPoint.y())
    point3 = QgsPoint(endPoint.x(), endPoint.y())
    point4 = QgsPoint(endPoint.x(), startPoint.y())

    self.rubberBand.addPoint(point1, False)
    self.rubberBand.addPoint(point2, False)
    self.rubberBand.addPoint(point3, False)
    self.rubberBand.addPoint(point4, True)	# true to update canvas
    self.rubberBand.show()

  def rectangle(self):
    if self.startPoint == None or self.endPoint == None:
      return None
    #elif self.startPoint.x() == self.endPoint.x() or self.startPoint.y() == self.endPoint.y():
    #  return None

    return QgsRectangle(self.startPoint, self.endPoint)

  def setRectangle(self, rect):
    if rect == self.rectangle():
      return False

    if rect == None:
      self.reset()
    else:
      self.startPoint = QgsPoint(rect.xMaximum(), rect.yMaximum())
      self.endPoint = QgsPoint(rect.xMinimum(), rect.yMinimum())
      self.showRect(self.startPoint, self.endPoint)
    return True
