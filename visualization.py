import math
import vtk
import numpy as np
import pyvista as pv
from pyvista.utilities import vtkmatrix_from_array, array_from_vtkmatrix
import depthmap

class Visualizer:
    def __init__(self, width, height, off_screen=True):
        self.width = width
        self.height = height
        self.off_screen = off_screen
        self.plotter = pv.Plotter(off_screen, window_size=[width, height])

        # if off screen, show has not been called and we must render
        # before extracting an image
        if self.plotter._first_time:
            self.plotter._on_first_render_request()
            self.plotter.render()

    def show(self, **kwargs):
        if not self.off_screen:
            self.plotter.store_image = True

            # non-blocking
            # self.plotter.show(auto_close=False, interactive_update=True)

            # blocking
            self.plotter.show(**kwargs)

    def add_geometry(self, data, **kwargs):
        """ Adding drawing primitives.

        Parameters
        ----------
        data : pyvista.Common or pyvista.MultiBlock
            Any PyVista or VTK mesh is supported. Also, any dataset
            that :func:`pyvista.wrap` can handle including NumPy arrays of XYZ
            points. Example `data = pyvista.read("mesh.ply")` function.
        """
        self.plotter.add_mesh(data, rgb=True, **kwargs)

    def update_view_point(self, intrinsics, extrinsics):
        self.update_view_point_intrinsics(intrinsics)
        self.update_view_point_extrinsics(extrinsics)
        self.plotter.renderer.ResetCameraClippingRange()
        self.plotter.render()

    def update_view_point_intrinsics(self, intrinsics):
        f  = intrinsics[0, 0]
        cx = intrinsics[0, 2]
        cy = intrinsics[1, 2]

        w = self.width
        h = self.height

        # principal point to window center (normalized coordinate system)
        wcx = -2.0 / w * cx + 1
        wcy =  2.0 / h * cy - 1
        self.plotter.camera.SetWindowCenter(wcx, wcy)

        # focal length to view angle
        view_angle = 180 / math.pi * (2.0 * math.atan2(h / 2.0, f))
        self.plotter.camera.SetViewAngle(view_angle)

    def update_view_point_extrinsics(self, extrinsics):
        # apply the transform to scene objects
        self.plotter.camera.SetModelTransformMatrix(
            vtkmatrix_from_array(extrinsics))

        # camera can stay at the origin because we are transforming the scene objects
        self.plotter.camera.SetPosition(0, 0, 0)

        # look in the +Z direction of the camera coordinate system
        self.plotter.camera.SetFocalPoint(0, 0, 1)

        # the camera Y axis points down
        self.plotter.camera.SetViewUp(0, -1, 0)

    def get_view_point(self):
        intrinsic = self.get_view_point_intrinsics()
        extrinsic = self.get_view_point_extrinsics()
        return intrinsic, extrinsic

    def get_view_point_extrinsics(self):
        extrinsic = self.plotter.camera.GetModelTransformMatrix()
        return array_from_vtkmatrix(extrinsic)  # convert to numpy

    def get_view_point_intrinsics(self):
        w = self.width
        h = self.height

        # focal length
        view_angle = self.plotter.camera.GetViewAngle()
        f = (h / 2.0) / math.tan(view_angle * math.pi / 360.0)

        # principal point
        wcx, wcy = self.plotter.camera.GetWindowCenter()
        cx = w / 2.0 * (1 - wcx)
        cy = h / 2.0 * (1 + wcy)

        intrinsic = np.array([[f, 0, cx],
                              [0, f, cy],
                              [0, 0, 1.]])
        return intrinsic

    def capture_screen_image(self, filename=None):
        """ Take screenshot at current camera position.

        Parameters
        ----------
        filename : str, optional
            Location to write image to. If None, no image is written to disk.

        Returns
        -------
        img :  numpy.ndarray
            Array containing pixel RGB
        """
        return self.plotter.screenshot(filename, return_img=True)

    def capture_depth_image(self,
                            filename=None,
                            fill_value=0,
                            reset_camera_clipping_range=True,
                            rtol=1.e-4,
                            max_depth_value=1000):

        # possible bug when `self.off_screen=True`
        if self.off_screen:
            self.show(auto_close=False, interactive_update=True)

        zval = self.plotter.get_image_depth(fill_value,
                                            reset_camera_clipping_range)

        # replace image values outside clipping range with `fill_value`.
        nar, far = self.plotter.camera.clipping_range
        indexes = np.logical_or(zval < -far, np.isclose(zval, -far, rtol=rtol))
        zval[indexes] = fill_value

        # values from `get_image_depth()` are negative to adhere to a
        # right-handed coordinate system. We need positive values.
        depth = - zval

        if filename is not None:
            depthmap.write_compressed(filename, depth, max_depth_value)

        return depth

    def ray_cast(self, x, y, intrinsic, extrinsic,
                 plot=False,
                 max_intersection_distance=1000):
        """ Get 3D measurement for the (x,y) pixel coordinate, using ray casting

        Parameters
        ----------
        x : int
            x-axis of the image pixel.
        y : int
            y-axis of the image pixel.
        intrinsic : numpy.ndarray (3, 3)
            Calibration camera matrix.
        extrinsic : numpy.ndarray (3, 4) or (4, 4)
            Camera pose (from world to camera).
        plot : bool, optional
            Plots ray trace results
        max_intersection_distance : int, optional
            This function computes the mesh interception from a start point to a
            stop point. This argument control the distance between these points.

        Returns
        -------
        point3D_world :  numpy.ndarray
            Array containing pixel RGB.
        depth_value : float
            Distance from camera center to computed 3D point.
        """
        point2D = np.array([x, y, 1])

        R = extrinsic[0:3,0:3]
        t = extrinsic[0:3,3]
        cam_center = - R.T @ t
        K_inv = np.linalg.inv(intrinsic)

        direction = R.T @ K_inv @ point2D
        direction = direction / np.linalg.norm(direction)

        start = cam_center
        stop  = cam_center + max_intersection_distance * direction

        # Perform ray casting
        point3D_world, ind = self.plotter.mesh.ray_trace(
            start, stop, first_point=True, plot=plot)

        # depth value: distance from camera center to computed 3D point
        depth_value = np.linalg.norm(cam_center - point3D_world)

        return point3D_world, depth_value


    def draw_camera(self, intrinsic, extrinsic, scale=0.1, color=None):
        """ Draw camera using triangles and lines.

        Parameters
        ----------
        intrinsic : numpy.ndarray (3, 3)
            Calibration camera matrix.
        extrinsic : numpy.ndarray (3, 4) or (4, 4)
            Camera pose (from world to camera).
        scale : float, optional
            Resize to scale, by default 0.1.
        color : list (3 values) or color name ("red"), optional
            Color of the image plane and pyramid lines, by default None.
        """
        # default color
        if color is None:
            color = [0.8, 0.2, 0.8]

        w = self.width
        h = self.height

        K = intrinsic

        # extrinsic: world to camera
        R = extrinsic[0:3, 0:3]
        t = extrinsic[0:3, 3]
        center = - R.T @ t

        # points in pixel
        points_pixel = [
            [0, 0, 1],
            [w, 0, 1],
            [0, h, 1],
            [w, h, 1],
        ]

        # pixel to camera coordinate system
        Kinv = np.linalg.inv(K / scale)
        points_cam = [Kinv @ p for p in points_pixel]

        # 3D points in world coordinate system
        points_in_world = [(R.T @ p - R.T@t) for p in points_cam]

        # axes
        x_axis = R.T @ np.array([1., 0., 0.])
        y_axis = R.T @ np.array([0., 1., 0.])
        z_axis = R.T @ np.array([0., 0., 1.])

        # draw axes
        x_axis_line = pv.Line(center, center + scale/2.0*x_axis)
        y_axis_line = pv.Line(center, center + scale/2.0*y_axis)
        z_axis_line = pv.Line(center, center + scale/2.0*z_axis)
        self.plotter.add_mesh(x_axis_line, line_width=3, color="red")
        self.plotter.add_mesh(y_axis_line, line_width=3, color="green")
        self.plotter.add_mesh(z_axis_line, line_width=3, color="blue")

        # display 3D points
        # v.plotter.add_points(np.array(points_in_world))

        # create camera plane using two triangles
        points = vtk.vtkPoints()
        for p in points_in_world:
            points.InsertNextPoint(p[0], p[1], p[2])

        triangle01 = vtk.vtkTriangle()
        triangle01.GetPointIds().SetId(0, 0)
        triangle01.GetPointIds().SetId(1, 1)
        triangle01.GetPointIds().SetId(2, 2)

        triangle02 = vtk.vtkTriangle()
        triangle02.GetPointIds().SetId(0, 1)
        triangle02.GetPointIds().SetId(1, 2)
        triangle02.GetPointIds().SetId(2, 3)

        triangles = vtk.vtkCellArray()
        triangles.InsertNextCell(triangle01)
        triangles.InsertNextCell(triangle02)

        trianglePolyData = vtk.vtkPolyData()
        trianglePolyData.SetPoints(points)
        trianglePolyData.SetPolys(triangles)

        self.plotter.add_mesh(trianglePolyData, color=color, opacity=0.65)

        # draw camera as a cone
        for p in points_in_world:
            line = pv.Line(center, p)
            self.plotter.add_mesh(line, line_width=3, color=color, opacity=0.5)
